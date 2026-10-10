import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch  # noqa: E402
import upload  # noqa: E402
from dataset_files import load_fingerprint, parse_patterns  # noqa: E402


class FakeApi:
    def __init__(self, files, download_failures=0, upload_failures=0):
        self.files = files
        self.download_failures = download_failures
        self.upload_failures = upload_failures
        self.uploads = []

    def dataset_download_files(self, dataset, path, force, quiet, unzip):
        if self.download_failures:
            self.download_failures -= 1
            raise RuntimeError("download blip")
        Path(path).mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(Path(path) / f"{dataset.split('/')[1]}.zip", "w") as bundle:
            for name, content in self.files.items():
                bundle.writestr(name, content)

    def dataset_create_version(self, folder, message, dir_mode, quiet):
        if self.upload_failures:
            self.upload_failures -= 1
            raise RuntimeError("upload blip")
        self.uploads.append((folder, message, sorted(p.name for p in Path(folder).iterdir())))


DECKS = {
    "00000073.csv": b"deck_num,name\r\n730001,a\r\n730002,b\r\n",
    "_last_deck_num.txt": b"730002",
}


@mock.patch.object(fetch, "RETRY_DELAYS_SECONDS", (0, 0, 0))
@mock.patch.object(upload, "RETRY_DELAYS_SECONDS", (0, 0, 0))
class RefreshTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / "repo" / "data"
        self.prints = self.tmp / "fingerprint.json"
        self.metadata = self.tmp / "repo" / "dataset-metadata.json"
        self.data.parent.mkdir()
        self.metadata.write_text('{"id": "ioexception/yugioh-decks"}')
        self.output = self.tmp / "github_output"

    def fetched(self, api=None):
        api = api or FakeApi(DECKS)
        fetch.fetch(api, "ioexception/yugioh-decks", self.data, self.prints)
        return api

    def run_upload(self, api, **kwargs):
        kwargs.setdefault("append_only", ["*.csv"])
        return upload.upload(api, self.data, self.prints, metadata=self.metadata,
                             github_output=self.output, **kwargs)

    def test_fetch_extracts_and_fingerprints(self):
        self.fetched(FakeApi(DECKS, download_failures=1))
        self.assertEqual((self.data / "00000073.csv").read_bytes(), DECKS["00000073.csv"])
        self.assertEqual(set(load_fingerprint(self.prints)), set(DECKS))
        self.assertEqual([p.name for p in self.data.parent.iterdir() if p.name.startswith(".fetch-")], [])

    def test_fetch_refuses_non_empty_directory_without_replace(self):
        self.data.mkdir()
        (self.data / "stale.csv").write_text("old")
        with self.assertRaises(SystemExit):
            self.fetched()
        self.assertTrue((self.data / "stale.csv").exists())

        fetch.fetch(FakeApi(DECKS), "ioexception/yugioh-decks", self.data, self.prints, replace=True)
        self.assertEqual(sorted(p.name for p in self.data.iterdir()), sorted(DECKS))

    def test_fetch_refuses_empty_dataset(self):
        with self.assertRaises(SystemExit):
            self.fetched(FakeApi({}))
        self.assertFalse(self.data.exists())

    def test_fetch_gives_up_after_retries(self):
        with self.assertRaises(SystemExit):
            self.fetched(FakeApi(DECKS, download_failures=3))

    def test_unchanged_skips_upload(self):
        api = self.fetched()
        self.assertFalse(self.run_upload(api))
        self.assertEqual(api.uploads, [])
        self.assertEqual(self.output.read_text(), "changed=false\n")

    def test_appended_rows_upload_with_metadata(self):
        api = self.fetched()
        with open(self.data / "00000073.csv", "ab") as handle:
            handle.write(b"730003,c\r\n")
        (self.data / "_last_deck_num.txt").write_text("730003")
        api.upload_failures = 1

        self.assertTrue(self.run_upload(api, message="test"))
        self.assertEqual(api.uploads, [(str(self.data), "test", sorted([*DECKS, "dataset-metadata.json"]))])
        self.assertEqual(self.output.read_text(), "changed=true\n")

    def test_new_file_uploads(self):
        api = self.fetched()
        (self.data / "00000074.csv").write_bytes(b"deck_num,name\r\n740000,d\r\n")
        self.assertTrue(self.run_upload(api))
        self.assertEqual(len(api.uploads), 1)

    def test_rewritten_append_only_file_is_refused(self):
        api = self.fetched()
        (self.data / "00000073.csv").write_bytes(b"deck_num,name\r\n730002,b\r\n730001,a\r\n730003,c\r\n")
        with self.assertRaisesRegex(SystemExit, "changed existing content"):
            self.run_upload(api)
        self.assertEqual(api.uploads, [])

    def test_deleted_or_shrunk_append_only_file_is_refused(self):
        api = self.fetched()
        (self.data / "00000073.csv").write_bytes(b"deck_num,name\r\n")
        with self.assertRaisesRegex(SystemExit, "shrank"):
            self.run_upload(api)
        (self.data / "00000073.csv").unlink()
        with self.assertRaisesRegex(SystemExit, "deleted"):
            self.run_upload(api)
        self.assertEqual(api.uploads, [])

    def test_files_outside_append_only_may_change(self):
        api = self.fetched()
        (self.data / "00000073.csv").write_bytes(b"deck_num,name\r\n1,z\r\n")
        self.assertTrue(self.run_upload(api, append_only=[]))
        self.assertEqual(len(api.uploads), 1)

    def test_missing_metadata_is_refused(self):
        api = self.fetched()
        (self.data / "_last_deck_num.txt").write_text("730009")
        self.metadata.unlink()
        with self.assertRaisesRegex(SystemExit, "missing"):
            self.run_upload(api)
        with self.assertRaisesRegex(SystemExit, "missing"):
            upload.upload(api, self.data, self.prints, metadata=None, append_only=["*.csv"])
        self.assertEqual(api.uploads, [])

    def test_dry_run_checks_but_does_not_upload(self):
        self.fetched()
        (self.data / "_last_deck_num.txt").write_text("730009")
        self.assertTrue(self.run_upload(None, dry_run=True))

    def test_metadata_is_not_part_of_the_fingerprint(self):
        api = self.fetched(FakeApi({**DECKS, "dataset-metadata.json": b"{}"}))
        (self.data / "dataset-metadata.json").write_text('{"changed": true}')
        self.assertFalse(self.run_upload(api))

    def test_parse_patterns(self):
        self.assertEqual(parse_patterns(" *.csv, data/*.txt\n"), ["*.csv", "data/*.txt"])
        self.assertEqual(parse_patterns(""), [])


if __name__ == "__main__":
    unittest.main()
