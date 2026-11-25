"""Generate valid Excel (.xlsx) test input using pure Python stdlib."""

import io
import zipfile
from pathlib import Path


def build_xlsx(headers: list[str], rows: list[list[str]], output_path: Path):
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

    sheet_data_xml = ["<sheetData>"]
    all_rows = [headers] + rows
    for r_idx, row_vals in enumerate(all_rows, start=1):
        sheet_data_xml.append(f'<row r="{r_idx}">')
        for c_idx, val in enumerate(row_vals):
            if val == "":
                continue
            coord = f"{_col_idx_to_letters(c_idx)}{r_idx}"
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

    ss_xml = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n',
        f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(shared_strings)}" uniqueCount="{len(shared_strings)}">',
    ]
    for s in shared_strings:
        escaped = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
        ss_xml.append(f"<si><t>{escaped}</t></si>")
    ss_xml.append("</sst>")

    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        '</Types>'
    )

    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets>'
        '<sheet name="Sheet1" sheetId="1" r:id="rId1"/>'
        '</sheets>'
        '</workbook>'
    )

    wb_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
        '</Relationships>'
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels_xml)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        zf.writestr("xl/sharedStrings.xml", "".join(ss_xml))

    print(f"Generated Excel file: {output_path} ({output_path.stat().st_size} bytes)")


if __name__ == "__main__":
    headers = ["Full Name", "Email Address", "Phone Number", "Company Name", "City", "State"]
    rows = [
        # Lead 1: The Pilot Record
        ["Sunil Rajput", "sunil@anikalabs.com", "+91-9876543210", "Anika Labs", "Indore", "MP"],
        # Lead 2: Valid Commercial Entity
        ["Amanda Lefton", "decfcclicensing@dec.ny.gov", "5184029056", "New York State Department of Environmental Conservation", "Albany", "NY"],
        # Lead 3: Valid Commercial Entity
        ["Katie Farmer", "telecomdeptfcc@bnsf.com", "9135514639", "BNSF RAILWAY CO.", "Kansas City", "KS"],
        # Lead 4: Valid Commercial Entity
        ["Jerry Mason", "jmason@cargill.com", "5073772526", "CARGILL FOODS INC.", "Minneapolis", "MN"],
        # Lead 5: Duplicate Domain (anikalabs.com) - tests deduplication
        ["Support Team", "contact@anikalabs.com", "+91-9876543210", "Anika Labs", "Indore", "MP"],
        # Lead 6: Free Mail Provider - tests garbage/personal filter
        ["Test User", "testuser@gmail.com", "9876543210", "Personal Account", "Delhi", "DL"],
        # Lead 7: Invalid Email Syntax - tests format filter
        ["Corrupt Lead", "not_a_valid_email_at_all", "0000000000", "Corrupt Inc", "Unknown", "XX"],
        # Lead 8: Non-existent Domain - tests DNS NXDOMAIN filter
        ["Phantom Lead", "fake@nonexistent-domain-xyz987654.org", "1112223333", "Phantom Corp", "Nowhere", "NA"],
    ]
    out_file = Path("data/input/Maam_Pilot_Records.xlsx")
    build_xlsx(headers, rows, out_file)
