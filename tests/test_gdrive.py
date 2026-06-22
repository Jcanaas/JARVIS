import unittest
from unittest.mock import Mock, patch

from actions import gdrive


class DrivePreviewTests(unittest.TestCase):
    @patch("actions.gdrive._get_service")
    def test_search_can_be_limited_to_current_folder(self, get_service):
        get_service.return_value.files.return_value.list.return_value.execute.return_value = {
            "files": []
        }

        gdrive.search_files("informe", folder_id="folder-id")

        query = get_service.return_value.files.return_value.list.call_args.kwargs["q"]
        self.assertIn("name contains 'informe'", query)
        self.assertIn("'folder-id' in parents", query)

    @patch("actions.gdrive._get_service")
    @patch("actions.gdrive.get_file_info")
    def test_large_file_is_not_downloaded(self, info, get_service):
        info.return_value = {
            "id": "large",
            "name": "video.mp4",
            "mimeType": "video/mp4",
            "size": str(20 * 1024 * 1024),
        }

        result = gdrive.get_file_preview("large", max_bytes=1024)

        self.assertEqual(result["kind"], "too_large")
        get_service.return_value.files.return_value.get_media.assert_not_called()

    @patch("googleapiclient.http.MediaIoBaseDownload")
    @patch("actions.gdrive._get_service")
    @patch("actions.gdrive.get_file_info")
    def test_google_document_is_exported_as_pdf(self, info, get_service, downloader_cls):
        info.return_value = {
            "id": "doc",
            "name": "Informe",
            "mimeType": "application/vnd.google-apps.document",
        }
        request = Mock()
        get_service.return_value.files.return_value.export_media.return_value = request

        def write_once():
            output = downloader_cls.call_args.args[0]
            output.write(b"%PDF-preview")
            return None, True

        downloader_cls.return_value.next_chunk.side_effect = write_once
        result = gdrive.get_file_preview("doc")

        self.assertEqual(result["kind"], "pdf")
        self.assertEqual(result["data"], b"%PDF-preview")
        get_service.return_value.files.return_value.export_media.assert_called_once_with(
            fileId="doc",
            mimeType="application/pdf",
        )

    def test_docx_text_is_extracted_locally(self):
        import io
        import zipfile

        document_xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>Texto interno</w:t></w:r></w:p></w:body></w:document>"
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", document_xml)

        text = gdrive._office_text_preview(
            buffer.getvalue(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "documento.docx",
        )

        self.assertEqual(text, "Texto interno")


if __name__ == "__main__":
    unittest.main()
