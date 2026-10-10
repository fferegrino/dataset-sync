import fnmatch
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dataset_sync import retry  # noqa: E402
from dataset_sync.backends import HuggingFace, Kaggle  # noqa: E402
from dataset_sync.fetch import fetch  # noqa: E402
from dataset_sync.files import load_state, parse_patterns  # noqa: E402
from dataset_sync.publish import publish  # noqa: E402

DECKS = {
    "00000073.csv": b"deck_num,name\r\n730001,a\r\n730002,b\r\n",
    "_last_deck_num.txt": b"730002",
}


def read_tree(folder):
    root = Path(folder)
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class FakeKaggleApi:
    """Stores the dataset the way Kaggle does: each version replaces every file."""

    def __init__(self, files, download_failures=0, upload_failures=0):
        self.files = dict(files)
        self.download_failures = download_failures
        self.upload_failures = upload_failures
        self.versions = []

    def dataset_download_files(self, dataset, path, force, quiet, unzip):
        if self.download_failures:
            self.download_failures -= 1
            raise RuntimeError("download blip")
        with zipfile.ZipFile(Path(path) / f"{dataset.split('/')[1]}.zip", "w") as bundle:
            for name, content in self.files.items():
                bundle.writestr(name, content)

    def dataset_create_version(self, folder, message, dir_mode, quiet):
        if self.upload_failures:
            self.upload_failures -= 1
            raise RuntimeError("upload blip")
        tree = read_tree(folder)
        self.versions.append((message, json.loads(tree.pop("dataset-metadata.json"))["id"]))
        self.files = tree


class FakeHfApi:
    """Stores the dataset the way the Hub does: uploads add or replace files, deletions are explicit."""

    def __init__(self, files=None, upload_failures=0):
        self.files = dict(files or {})
        self.upload_failures = upload_failures
        self.commits = []

    def snapshot_download(self, repo_id, repo_type, local_dir):
        assert repo_type == "dataset"
        for name, content in self.files.items():
            target = Path(local_dir) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        (Path(local_dir) / ".cache" / "huggingface").mkdir(parents=True)
        (Path(local_dir) / ".cache" / "huggingface" / "download.lock").write_text("")

    def list_repo_files(self, repo_id, repo_type):
        return list(self.files)

    def upload_folder(self, repo_id, repo_type, folder_path, commit_message, delete_patterns):
        if self.upload_failures:
            self.upload_failures -= 1
            raise RuntimeError("upload blip")
        tree = read_tree(folder_path)
        for name in list(self.files):
            if name not in tree and name != ".gitattributes" and any(
                fnmatch.fnmatch(name, pattern) for pattern in delete_patterns or []
            ):
                del self.files[name]
        self.files.update(tree)
        self.commits.append(commit_message)


HUB_EXTRAS = {"README.md": b"old card", ".gitattributes": b"*.csv filter=lfs"}


@mock.patch.object(retry, "RETRY_DELAYS_SECONDS", (0, 0, 0))
class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.data = self.repo / "data"
        self.state = self.tmp / "state.json"
        self.metadata = self.repo / "dataset-metadata.json"
        self.metadata.write_text('{"id": "ioexception/yugioh-decks"}')
        self.card = self.repo / "dataset-card.md"
        self.card.write_text("---\nlicense: cc-by-sa-3.0\n---\n# Decks\n")
        self.output = self.tmp / "github_output"
        self.hub = FakeHfApi({**DECKS, **HUB_EXTRAS})
        self.kaggle = FakeKaggleApi(DECKS)

    def huggingface(self):
        return HuggingFace("fferegrino/yugioh-decks", card=self.card, api=self.hub)

    def kaggle_backend(self):
        return Kaggle("ioexception/yugioh-decks", metadata=self.metadata, api=self.kaggle)

    def destinations(self):
        return [self.huggingface(), self.kaggle_backend()]

    def fetch_from(self, backend, **kwargs):
        return fetch(backend, self.data, self.state, **kwargs)

    def publish(self, **kwargs):
        kwargs.setdefault("append_only", ["*.csv"])
        return publish(kwargs.pop("destinations", None) or self.destinations(), self.data, self.state,
                       github_output=self.output, **kwargs)

    def append_deck(self):
        with open(self.data / "00000073.csv", "ab") as handle:
            handle.write(b"730003,c\r\n")
        (self.data / "_last_deck_num.txt").write_text("730003")

    # fetch

    def test_fetch_from_hub_keeps_only_data(self):
        self.fetch_from(self.huggingface())
        self.assertEqual(read_tree(self.data), DECKS)
        state = load_state(self.state)
        self.assertEqual(state["source"], "huggingface")
        self.assertEqual(set(state["fingerprint"]), set(DECKS))
        self.assertEqual([p.name for p in self.repo.iterdir() if p.name.startswith(".dataset-sync")], [])

    def test_fetch_from_kaggle_retries(self):
        self.kaggle.download_failures = 1
        self.fetch_from(self.kaggle_backend())
        self.assertEqual(read_tree(self.data), DECKS)
        self.assertEqual(load_state(self.state)["source"], "kaggle")

    def test_fetch_gives_up_after_retries(self):
        self.kaggle.download_failures = 3
        with self.assertRaisesRegex(SystemExit, "download"):
            self.fetch_from(self.kaggle_backend())

    def test_fetch_refuses_non_empty_directory_without_replace(self):
        self.data.mkdir()
        (self.data / "stale.csv").write_text("old")
        with self.assertRaises(SystemExit):
            self.fetch_from(self.huggingface())
        self.assertTrue((self.data / "stale.csv").exists())
        self.fetch_from(self.huggingface(), replace=True)
        self.assertEqual(read_tree(self.data), DECKS)

    def test_fetch_refuses_empty_dataset(self):
        self.hub.files = dict(HUB_EXTRAS)
        with self.assertRaisesRegex(SystemExit, "no files"):
            self.fetch_from(self.huggingface())
        self.assertFalse(self.data.exists())

    # publish

    def test_unchanged_uploads_nowhere_when_hub_is_source(self):
        self.fetch_from(self.huggingface())
        self.assertEqual(self.publish(), [])
        self.assertEqual((self.hub.commits, self.kaggle.versions), ([], []))
        self.assertEqual(self.output.read_text(), "changed=false\nuploaded=\n")

    def test_changes_reach_every_destination_with_their_own_metadata(self):
        self.fetch_from(self.huggingface())
        self.append_deck()
        self.assertEqual(self.publish(message="test"), ["kaggle", "huggingface"])

        expected = read_tree(self.data)
        self.assertEqual(self.kaggle.files, expected)
        self.assertEqual(self.kaggle.versions, [("test", "ioexception/yugioh-decks")])
        self.assertEqual(self.hub.files, {**expected, "README.md": self.card.read_bytes(),
                                          ".gitattributes": HUB_EXTRAS[".gitattributes"]})
        self.assertEqual(set(read_tree(self.data)), set(DECKS), "metadata leaked into the data directory")
        self.assertEqual(self.output.read_text(), "changed=true\nuploaded=kaggle,huggingface\n")

    def test_failed_mirror_leaves_source_untouched(self):
        self.fetch_from(self.huggingface())
        self.append_deck()
        self.kaggle.upload_failures = 3
        with self.assertRaisesRegex(SystemExit, "kaggle upload"):
            self.publish()
        self.assertEqual(self.hub.commits, [])

    def test_source_is_uploaded_last_when_kaggle_is_source(self):
        self.fetch_from(self.kaggle_backend())
        self.append_deck()
        self.assertEqual(self.publish(), ["huggingface", "kaggle"])

    def test_unchanged_still_seeds_hub_mirror(self):
        self.hub.files = {}
        self.fetch_from(self.kaggle_backend())
        self.assertEqual(self.publish(), ["huggingface"])
        self.assertEqual(self.kaggle.versions, [])
        self.assertEqual(self.hub.files, {**DECKS, "README.md": self.card.read_bytes()})

    def test_force_reuploads_kaggle_mirror(self):
        self.fetch_from(self.huggingface())
        self.assertEqual(self.publish(force=True), ["kaggle", "huggingface"])

    def test_hub_drops_files_removed_locally_but_keeps_repo_files(self):
        self.hub.files["00000099.csv"] = b"gone"
        self.fetch_from(self.huggingface())
        (self.data / "00000099.csv").unlink()
        self.publish(destinations=[HuggingFace("fferegrino/yugioh-decks", api=self.hub)], append_only=[])
        self.assertEqual(self.hub.files, {**DECKS, **HUB_EXTRAS})

    def test_append_only_violations_block_every_upload(self):
        self.fetch_from(self.huggingface())
        (self.data / "00000073.csv").write_bytes(b"deck_num,name\r\n730002,b\r\n730001,a\r\n730003,c\r\n")
        with self.assertRaisesRegex(SystemExit, "changed existing content"):
            self.publish()
        (self.data / "00000073.csv").write_bytes(b"deck_num,name\r\n")
        with self.assertRaisesRegex(SystemExit, "shrank"):
            self.publish()
        (self.data / "00000073.csv").unlink()
        with self.assertRaisesRegex(SystemExit, "deleted"):
            self.publish()
        self.assertEqual((self.hub.commits, self.kaggle.versions), ([], []))

    def test_files_outside_append_only_may_change(self):
        self.fetch_from(self.huggingface())
        (self.data / "00000073.csv").write_bytes(b"deck_num,name\r\n1,z\r\n")
        self.assertEqual(self.publish(append_only=[]), ["kaggle", "huggingface"])

    def test_invalid_destination_blocks_every_upload(self):
        self.fetch_from(self.huggingface())
        self.append_deck()
        self.metadata.write_text('{"id": "ioexception/yugioh-cards"}')
        with self.assertRaisesRegex(SystemExit, "not 'ioexception/yugioh-decks'"):
            self.publish()
        self.metadata.unlink()
        with self.assertRaisesRegex(SystemExit, "Kaggle needs"):
            self.publish()
        self.card.unlink()
        with self.assertRaisesRegex(SystemExit, "card"):
            self.publish(destinations=[self.huggingface()])
        self.assertEqual((self.hub.commits, self.kaggle.versions), ([], []))

    def test_missing_credentials_block_every_upload(self):
        self.fetch_from(self.huggingface())
        self.append_deck()
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(SystemExit, "KAGGLE"):
                self.publish(destinations=[self.huggingface(), Kaggle("ioexception/yugioh-decks", self.metadata)])
            with self.assertRaisesRegex(SystemExit, "HF_TOKEN"):
                self.publish(destinations=[HuggingFace("fferegrino/yugioh-decks"), self.kaggle_backend()])
        self.assertEqual((self.hub.commits, self.kaggle.versions), ([], []))

    def test_publish_without_fetch_needs_force(self):
        self.data.mkdir()
        (self.data / "00000073.csv").write_bytes(DECKS["00000073.csv"])
        with self.assertRaisesRegex(SystemExit, "fetch"):
            self.publish()
        self.assertEqual(self.publish(force=True), ["huggingface", "kaggle"])

    def test_dry_run_checks_but_does_not_upload(self):
        self.fetch_from(self.huggingface())
        self.append_deck()
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(self.publish(dry_run=True,
                                          destinations=[HuggingFace("fferegrino/yugioh-decks"),
                                                        Kaggle("ioexception/yugioh-decks", self.metadata)]), [])

    def test_retries_transient_upload_failures(self):
        self.fetch_from(self.huggingface())
        self.append_deck()
        self.hub.upload_failures = 1
        self.kaggle.upload_failures = 2
        self.assertEqual(self.publish(), ["kaggle", "huggingface"])

    def test_parse_patterns(self):
        self.assertEqual(parse_patterns(" *.csv, data/*.txt\n"), ["*.csv", "data/*.txt"])
        self.assertEqual(parse_patterns(""), [])


if __name__ == "__main__":
    unittest.main()
