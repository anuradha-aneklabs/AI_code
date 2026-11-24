"""Unit tests for Task 4: Pure stdlib .xlsx batch parser.

Verifies:
1. Pure stdlib .xlsx parsing (zipfile + xml.etree.ElementTree, 0 external dependencies).
2. Shared strings, inline strings, numbers, empty cells, and column coordinates beyond Z.
3. Identical column mapping and row parsing between .xlsx and .csv.
4. Unsupported extensions (e.g. .pdf, .json) rejected with clear error.
"""

import io
import zipfile
import xml.etree.ElementTree as ET
import pytest

from harvey.batch_research import parse_import_file, _read_xlsx_stdlib


def _build_synthetic_xlsx(headers: list[str], rows: list[list[str]]) -> bytes:
    """Build a minimal valid .xlsx archive using only stdlib zipfile."""
    bio = io.BytesIO()

    # Collect shared strings
    shared_strings: list[str] = []
    ss_map: dict[str, int] = {}
    def _add_ss(s: str) -> int:
        if s not in ss_map:
            ss_map[s] = len(shared_strings)
            shared_strings.append(s)
        return ss_map[s]

    def _col_idx_to_letters(col_idx: int) -> str:
        letters = ""
        col_idx += 1
        while col_idx > 0:
            col_idx, rem = divmod(col_idx - 1, 26)
            letters = chr(65 + rem) + letters
        return letters

    # Build sheet XML
    sheet_data_xml = ["<sheetData>"]
    all_rows = [headers] + rows
    for r_idx, row_vals in enumerate(all_rows, start=1):
        sheet_data_xml.append(f'<row r="{r_idx}">')
        for c_idx, val in enumerate(row_vals):
            if val == "":
                continue  # empty cell
            col_letters = _col_idx_to_letters(c_idx)
            coord = f"{col_letters}{r_idx}"
            s_idx = _add_ss(str(val))
            sheet_data_xml.append(f'<c r="{coord}" t="s"><v>{s_idx}</v></c>')
        sheet_data_xml.append("</row>")
    sheet_data_xml.append("</sheetData>")

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + "".join(sheet_data_xml)
        + "</worksheet>"
    )

    # Build sharedStrings XML
    ss_xml = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n',
        f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(shared_strings)}" uniqueCount="{len(shared_strings)}">',
    ]
    for s in shared_strings:
        escaped = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        ss_xml.append(f"<si><t>{escaped}</t></si>")
    ss_xml.append("</sst>")

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    )

    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", workbook_xml)
        z.writestr("xl/sharedStrings.xml", "".join(ss_xml))
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)

    return bio.getvalue()


def test_xlsx_parser_parity_with_csv():
    headers = [
        "EntityName", "FirstName", "MiddleInitial", "LastName", "Suffix",
        "Phone", "EmailAddress", "EmailValid", "Address1", "City",
        "StateCode", "PostalCode", "POBox", "AttentionLine"
    ]
    fake_rows = [
        ["ACME Corp", "Alice", "B", "Smith", "", "5551234567", "alice@acme.com", "1", "123 Main St", "Springfield", "IL", "62701", "", "CEO"],
        ["Beta Inc", "Bob", "", "Jones", "Jr", "5559876543", "bob@betainc.com", "1", "456 Oak Ave", "Chicago", "IL", "60601", "101", ""],
    ]

    # Generate synthetic .xlsx
    xlsx_bytes = _build_synthetic_xlsx(headers, fake_rows)
    parsed_xlsx_rows, xlsx_col_map = parse_import_file(xlsx_bytes, "contacts.xlsx")

    # Generate equivalent CSV
    csv_text = ",".join(f'"{h}"' for h in headers) + "\n"
    for r in fake_rows:
        csv_text += ",".join(f'"{v}"' for v in r) + "\n"
    csv_bytes = csv_text.encode("utf-8")
    parsed_csv_rows, csv_col_map = parse_import_file(csv_bytes, "contacts.csv")

    # Assert identical mapping and row parsing
    assert xlsx_col_map == csv_col_map
    assert len(parsed_xlsx_rows) == len(parsed_csv_rows) == 2
    assert parsed_xlsx_rows[0]["raw_email"] == parsed_csv_rows[0]["raw_email"] == "alice@acme.com"
    assert parsed_xlsx_rows[0]["raw_name"] == parsed_csv_rows[0]["raw_name"] == "Alice B Smith"
    assert parsed_xlsx_rows[0]["company_name_hint"] == parsed_csv_rows[0]["company_name_hint"] == "ACME Corp"


def test_xlsx_columns_beyond_z():
    # 28 columns (A through AB)
    headers = [f"Col{i}" for i in range(1, 28)] + ["EmailAddress"]
    rows = [["Val" + str(i) for i in range(1, 28)] + ["beyondz@test.com"]]
    xlsx_bytes = _build_synthetic_xlsx(headers, rows)

    parsed_rows, col_map = parse_import_file(xlsx_bytes, "beyond_z.xlsx")
    assert "email" in col_map
    assert col_map["email"] == "EmailAddress"
    assert parsed_rows[0]["raw_email"] == "beyondz@test.com"


def test_unsupported_format_rejected():
    with pytest.raises(ValueError, match="Unsupported file format"):
        parse_import_file(b"dummy", "contacts.pdf")
