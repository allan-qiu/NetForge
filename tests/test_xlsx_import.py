import io
import unittest
import zipfile

import v2_http


NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


class XlsxImportTests(unittest.TestCase):
    def test_template_columns_are_parsed_into_a_question(self):
        headers = ["external_id", "type", "topic", "difficulty", "question", "option_a", "option_b", "answer", "explanation", "source", "status"]
        values = ["xlsx-001", "single", "网络基础", "easy", "示例题", "选项一", "选项二", "B", "提交后解析", "可追溯来源", "draft"]
        rows = []
        for row_index, row in enumerate((headers, values), 1):
            cells = []
            for column_index, value in enumerate(row):
                column = chr(ord("A") + column_index)
                cells.append(f'<c r="{column}{row_index}" t="inlineStr"><is><t>{value}</t></is></c>')
            rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
        xml = f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{NS}"><sheetData>{"".join(rows)}</sheetData></worksheet>'
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("xl/worksheets/sheet1.xml", xml)
        parsed = v2_http._xlsx_rows(buffer.getvalue())
        question = v2_http._row_question(parsed[0])
        self.assertEqual(question["id"], "xlsx-001")
        self.assertEqual(question["answer"], ["B"])
        self.assertEqual(question["options"][1]["text"], "选项二")
        self.assertEqual(question["__import_status"], "draft")


if __name__ == "__main__":
    unittest.main()
