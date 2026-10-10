import glob
import json
import os
import tempfile
import zipfile
from pathlib import Path

from dataset_sync.files import data_files, is_data_path, staged


class Kaggle:
    name = "kaggle"
    # Every upload creates a new dataset version, so Kaggle only receives data that actually changed.
    idempotent = False

    def __init__(self, dataset, metadata=None, api=None):
        self.dataset = dataset
        self.metadata = metadata
        self._api = api

    @property
    def api(self):
        if self._api is None:
            self.check_credentials()
            from kaggle import api  # authenticates on import

            self._api = api
        return self._api

    def check_credentials(self):
        if self._api is not None:
            return
        has_key = os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY")
        if not (has_key or os.environ.get("KAGGLE_API_TOKEN")):
            raise SystemExit("Set KAGGLE_USERNAME and KAGGLE_KEY, or KAGGLE_API_TOKEN, to use Kaggle")

    def validate(self):
        if not self.metadata or not Path(self.metadata).is_file():
            raise SystemExit(f"Kaggle needs {self.metadata or 'a dataset-metadata.json'} to know which dataset to update")
        metadata_id = json.loads(Path(self.metadata).read_text()).get("id")
        if metadata_id != self.dataset:
            raise SystemExit(f"{self.metadata} is for {metadata_id!r}, not {self.dataset!r}")

    def download(self, destination):
        with tempfile.TemporaryDirectory() as scratch:
            self.api.dataset_download_files(self.dataset, path=scratch, force=True, quiet=True, unzip=False)
            archives = sorted(Path(scratch).glob("*.zip"))
            if len(archives) != 1:
                raise RuntimeError(f"expected one archive from Kaggle, found {[archive.name for archive in archives]}")
            with zipfile.ZipFile(archives[0]) as bundle:
                bundle.extractall(destination)

    def listing(self):
        files = {}
        page_token = None
        while True:
            response = self.api.dataset_list_files(self.dataset, page_token=page_token, page_size=200)
            if response.error_message:
                raise RuntimeError(f"Kaggle could not list {self.dataset}: {response.error_message}")
            for entry in response.files:
                files[entry.name] = {"size": entry.total_bytes, "sha256": None}
            page_token = response.next_page_token
            if not page_token:
                return files

    def pending_deletions(self, directory):
        # A new version replaces every file, so anything Kaggle has that the directory lacks would disappear.
        local = {name for name, _path in data_files(directory)}
        return sorted(name for name in self.listing() if is_data_path(name) and name not in local)

    def upload(self, directory, message):
        with staged(directory, {"dataset-metadata.json": self.metadata}) as folder:
            self.api.dataset_create_version(str(folder), message, dir_mode="zip", quiet=False)


class HuggingFace:
    name = "huggingface"
    # upload_folder skips files whose content is already on the Hub, so syncing on every run is cheap and safe.
    idempotent = True

    def __init__(self, dataset, card=None, api=None):
        self.dataset = dataset
        self.card = card
        self._api = api

    @property
    def api(self):
        if self._api is None:
            from huggingface_hub import HfApi

            self._api = HfApi(token=os.environ.get("HF_TOKEN") or None)
        return self._api

    def check_credentials(self):
        # Public datasets download anonymously; only uploads need a token.
        if self._api is None and not os.environ.get("HF_TOKEN"):
            raise SystemExit("Set HF_TOKEN to upload to Hugging Face")

    def validate(self):
        if self.card and not Path(self.card).is_file():
            raise SystemExit(f"Dataset card {self.card} does not exist")

    def download(self, destination):
        self.api.snapshot_download(repo_id=self.dataset, repo_type="dataset", local_dir=str(destination))

    def listing(self):
        files = {}
        for entry in self.api.list_repo_tree(self.dataset, recursive=True, repo_type="dataset"):
            if getattr(entry, "size", None) is None:  # folders have no size
                continue
            lfs = getattr(entry, "lfs", None)
            files[entry.path] = {"size": entry.size, "sha256": lfs.sha256 if lfs else None}
        return files

    def pending_deletions(self, directory):
        # A Kaggle version replaces the whole dataset; deleting files that are gone locally gives the Hub the same meaning.
        local = {name for name, _path in data_files(directory)}
        return sorted(name for name in self.listing() if is_data_path(name) and name not in local)

    def upload(self, directory, message):
        stale = self.pending_deletions(directory)
        extras = {"README.md": self.card} if self.card else {}
        with staged(directory, extras) as folder:
            self.api.upload_folder(
                repo_id=self.dataset,
                repo_type="dataset",
                folder_path=str(folder),
                commit_message=message,
                delete_patterns=[glob.escape(name) for name in stale] or None,
            )


def configured(kaggle_dataset=None, kaggle_metadata=None, huggingface_dataset=None, huggingface_card=None):
    backends = {}
    if huggingface_dataset:
        backends["huggingface"] = HuggingFace(huggingface_dataset, card=huggingface_card or None)
    if kaggle_dataset:
        backends["kaggle"] = Kaggle(kaggle_dataset, metadata=kaggle_metadata or None)
    return backends


def add_arguments(parser):
    parser.add_argument("--kaggle-dataset", help="owner/slug of the Kaggle dataset")
    parser.add_argument("--kaggle-metadata", default="dataset-metadata.json", help="Kaggle dataset-metadata.json")
    parser.add_argument("--huggingface-dataset", help="owner/name of the Hugging Face dataset repository")
    parser.add_argument("--huggingface-card", help="README.md dataset card to upload to Hugging Face")


def from_arguments(args):
    return configured(args.kaggle_dataset, args.kaggle_metadata, args.huggingface_dataset, args.huggingface_card)
