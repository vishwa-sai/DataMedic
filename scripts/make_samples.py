"""Generate the sample messy datasets in /samples (deterministic).

    python scripts/make_samples.py
"""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "samples"

HEADER = ["Customer ID", "Full Name ", "Email", "Phone", "Signup Date", "Amount ($)", "City", "Notes", ""]
ROWS = [
    ["001", "  Alice Johnson", "alice@example.com", "0123456789", "2023-01-15", "$1,200.50", "Paris", "VIP", ""],
    ["002", "Bob   Smith", "BOB@Example.COM", "0987654321", "15/01/2023", "950", "London ", "", ""],
    ["003", "Carol White", "carol@example", "0555123456", "Jan 17, 2023", "1,000", "Berlin", "N/A", ""],
    ["004", "David Brown", "", "0444987654", "2023/01/18", "", "Paris", "", ""],
    ["001", "  Alice Johnson", "alice@example.com", "0123456789", "2023-01-15", "$1,200.50", "Paris", "VIP", ""],
    ["005", "Eve Black", "eve@example.com", "", "19-Jan-2023", "$310.00", "", "late payer", ""],
    ["006", "Frank Green", "frank@@example.com", "0333111222", "2023-01-20", "75.25", "Madrid", "", ""],
    ["007", "Grace Hall", "grace@example.com", "0222333444", "21 January 2023", "1 000", "Rome", "none", ""],
    ["002", "Bob Smith", "bob@example.com", "0987654321", "2023-01-15", "950", "London", "", ""],
    ["008", "Heidi King", "heidi@example.com", "0111222333", "2023-01-23 14:30:00", "$2,500", "Vienna", "", ""],
]
# the header has a blank last name and a duplicate-by-case pair on purpose
HEADER2 = ["Customer ID", "Full Name ", "Email", "Phone", "Signup Date", "Amount ($)", "City", "Notes", ""]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with open(OUT / "messy_customers.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(ROWS)

    # Same data as XLSX (real numbers/dates mixed with text) and legacy XLS
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Customers"
    ws.append(HEADER)
    for r in ROWS:
        ws.append(r)
    ws["E3"] = dt.date(2023, 1, 16)           # a real Excel date among text dates
    ws["F4"] = 1000                           # a real number among text numbers
    wb.create_sheet("Ignored").append(["not", "analysed"])
    wb.save(OUT / "messy_customers.xlsx")

    import xlwt
    xl = xlwt.Workbook()
    sh = xl.add_sheet("Customers")
    for c, h in enumerate(HEADER):
        sh.write(0, c, h)
    for r, row in enumerate(ROWS, start=1):
        for c, v in enumerate(row):
            sh.write(r, c, v)
    xl.save(str(OUT / "messy_customers.xls"))

    # Dates / ambiguity showcase
    with open(OUT / "dates_and_numbers.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "ship_date", "ambiguous_date", "price", "zip"])
        w.writerows([
            ["A1", "13/02/2023", "01/02/2023", "$1,234.00", "01234"],
            ["A2", "02/03/2023", "03/04/2023", "€99", "02134"],
            ["A3", "2023-03-04", "05/06/2023", "10", "90210"],
            ["A4", "5 Mar 2023", "07/08/2023", "7.5", "00501"],
            ["A5", "28/02/2023", "09/10/2023", "", "10001"],
        ])

    # Latin-1 / cp1252 encoded file
    (OUT / "encoding_cp1252.csv").write_bytes("name,city\nJosé,Málaga\nZoë,Zürich\n".encode("cp1252"))

    # Malformed CSV (a row has too many fields)
    (OUT / "malformed.csv").write_text("a,b,c\n1,2,3\n4,5,6,7,8\n", encoding="utf-8")
    (OUT / "empty.csv").write_bytes(b"")
    print("Samples written to", OUT)


if __name__ == "__main__":
    main()
