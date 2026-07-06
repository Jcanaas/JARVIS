import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from actions import file_processor


class DetectTypeTests(unittest.TestCase):
    def test_image_types(self):
        for ext in [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"]:
            path = Path(f"test{ext}")
            result = file_processor._detect_type(path)
            self.assertEqual(result, "image", f"Failed for {ext}")

    def test_pdf_type(self):
        result = file_processor._detect_type(Path("document.pdf"))
        self.assertEqual(result, "pdf")

    def test_docx_type(self):
        result = file_processor._detect_type(Path("document.docx"))
        self.assertEqual(result, "docx")

    def test_text_types(self):
        for ext in [".txt", ".md", ".rst", ".log"]:
            path = Path(f"file{ext}")
            result = file_processor._detect_type(path)
            self.assertEqual(result, "text", f"Failed for {ext}")

    def test_spreadsheet_types(self):
        self.assertEqual(file_processor._detect_type(Path("data.csv")), "csv")
        self.assertEqual(file_processor._detect_type(Path("data.xlsx")), "excel")
        self.assertEqual(file_processor._detect_type(Path("data.xls")), "excel")
        self.assertEqual(file_processor._detect_type(Path("data.ods")), "excel")

    def test_code_types(self):
        for ext in [".py", ".js", ".ts", ".go", ".java", ".cpp", ".c", ".rs"]:
            path = Path(f"file{ext}")
            result = file_processor._detect_type(path)
            self.assertEqual(result, "code", f"Failed for {ext}")

    def test_audio_types(self):
        for ext in [".mp3", ".wav", ".flac", ".m4a", ".aac"]:
            path = Path(f"audio{ext}")
            result = file_processor._detect_type(path)
            self.assertEqual(result, "audio", f"Failed for {ext}")

    def test_video_types(self):
        for ext in [".mp4", ".avi", ".mkv", ".mov", ".webm"]:
            path = Path(f"video{ext}")
            result = file_processor._detect_type(path)
            self.assertEqual(result, "video", f"Failed for {ext}")

    def test_archive_types(self):
        self.assertEqual(file_processor._detect_type(Path("archive.zip")), "archive")
        self.assertEqual(file_processor._detect_type(Path("archive.rar")), "archive")
        self.assertEqual(file_processor._detect_type(Path("archive.7z")), "archive")
        self.assertEqual(file_processor._detect_type(Path("archive.tar")), "archive")

    def test_pptx_type(self):
        result = file_processor._detect_type(Path("slides.pptx"))
        self.assertEqual(result, "pptx")

    def test_json_type(self):
        result = file_processor._detect_type(Path("data.json"))
        self.assertEqual(result, "json")

    def test_unknown_type(self):
        result = file_processor._detect_type(Path("file.unknown"))
        self.assertEqual(result, "unknown")

    def test_uppercase_extensions(self):
        result = file_processor._detect_type(Path("FILE.PDF"))
        self.assertEqual(result, "pdf")


class FileSizeStrTests(unittest.TestCase):
    def test_bytes_formatting(self):
        with tempfile.NamedTemporaryFile() as tmp:
            tmp.write(b"x" * 500)
            tmp.flush()
            result = file_processor._file_size_str(Path(tmp.name))
            self.assertIn("B", result)

    def test_kilobytes_formatting(self):
        with tempfile.NamedTemporaryFile() as tmp:
            tmp.write(b"x" * 5000)
            tmp.flush()
            result = file_processor._file_size_str(Path(tmp.name))
            self.assertTrue(
                "KB" in result or "B" in result,
                f"Expected KB or B in {result}"
            )

    def test_megabytes_formatting(self):
        with tempfile.NamedTemporaryFile() as tmp:
            tmp.write(b"x" * 5000000)
            tmp.flush()
            result = file_processor._file_size_str(Path(tmp.name))
            self.assertTrue(
                "MB" in result or "KB" in result,
                f"Expected MB or KB in {result}"
            )


class OutputPathTests(unittest.TestCase):
    def test_creates_suffixed_path(self):
        src = Path("document.pdf")
        result = file_processor._output_path(src, "processed")
        self.assertEqual(str(result.name), "document_processed.pdf")
        self.assertEqual(result.suffix, ".pdf")

    def test_creates_new_extension(self):
        src = Path("image.png")
        result = file_processor._output_path(src, "resized", ".jpg")
        self.assertEqual(str(result.name), "image_resized.jpg")
        self.assertEqual(result.suffix, ".jpg")

    def test_preserves_parent_directory(self):
        src = Path("subdir/file.txt")
        result = file_processor._output_path(src, "new")
        self.assertIn("subdir", str(result.parent))


class FileProcessorMainTests(unittest.TestCase):
    def test_rejects_missing_file_path(self):
        result = file_processor.file_processor(parameters={})

        self.assertIn("No file path", result)

    def test_rejects_nonexistent_file(self):
        result = file_processor.file_processor(
            parameters={"file_path": "/nonexistent/file.txt"}
        )

        self.assertIn("not found", result.lower())

    def test_rejects_directory_instead_of_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = file_processor.file_processor(
                parameters={"file_path": tmpdir}
            )

            self.assertIn("not a file", result.lower())

    def test_handles_unknown_action_gracefully(self):
        with tempfile.NamedTemporaryFile(suffix=".txt") as tmp:
            result = file_processor.file_processor(
                parameters={
                    "file_path": tmp.name,
                    "action": "unknown_action_xyz"
                }
            )

            # Should return error message, not crash
            self.assertIsInstance(result, str)

    @patch("actions.file_processor._detect_type")
    def test_logs_file_processing_info(self, mock_detect):
        mock_detect.return_value = "txt"
        with tempfile.NamedTemporaryFile(suffix=".txt") as tmp:
            with patch("builtins.print") as mock_print:
                result = file_processor.file_processor(
                    parameters={
                        "file_path": tmp.name,
                        "action": "summarize"
                    }
                )

                # Should have called print with logging info
                mock_print.assert_called()

    def test_processes_text_file_summarize(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=True) as tmp:
            tmp.write("Hello world. This is a test file. It has some content.")
            tmp.flush()
            with patch("actions.file_processor._process_text_doc", return_value="Summary: Hello world"):
                result = file_processor.file_processor(
                    parameters={
                        "file_path": tmp.name,
                        "action": "summarize"
                    }
                )
                self.assertEqual(result, "Summary: Hello world")


if __name__ == "__main__":
    unittest.main()
