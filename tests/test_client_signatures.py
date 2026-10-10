"""The sync tests use hand-written fakes; these check the calls also match the real client libraries' signatures."""
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dataset_sync.backends import HuggingFace, Kaggle  # noqa: E402

try:
    from huggingface_hub import HfApi
except ImportError:
    HfApi = None

try:
    # Importing the kaggle package tries to authenticate and prints instructions when it cannot.
    with redirect_stdout(io.StringIO()):
        from kaggle.api.kaggle_api_extended import KaggleApi
except ImportError:
    KaggleApi = None


class ClientSignatureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / "data"
        self.data.mkdir()
        (self.data / "00000001.csv").write_text("deck_num\n1\n")
        self.metadata = self.tmp / "dataset-metadata.json"
        self.metadata.write_text('{"id": "owner/decks"}')

    @unittest.skipIf(HfApi is None, "huggingface_hub is not installed")
    def test_huggingface_calls(self):
        api = mock.create_autospec(HfApi, instance=True)
        api.list_repo_files.return_value = ["00000001.csv", "old.csv", "README.md"]
        backend = HuggingFace("owner/decks", api=api)
        backend.download(self.tmp / "download")
        backend.upload(self.data, "message")
        self.assertEqual(api.upload_folder.call_args.kwargs["delete_patterns"], ["old.csv"])

    @unittest.skipIf(KaggleApi is None, "kaggle is not installed")
    def test_kaggle_calls(self):
        api = mock.create_autospec(KaggleApi, instance=True)
        backend = Kaggle("owner/decks", metadata=self.metadata, api=api)
        with self.assertRaisesRegex(RuntimeError, "expected one archive"):
            backend.download(self.tmp / "download")  # the mock writes no archive, but the call itself must bind
        api.dataset_download_files.assert_called_once()
        backend.upload(self.data, "message")
        api.dataset_create_version.assert_called_once()


if __name__ == "__main__":
    unittest.main()
