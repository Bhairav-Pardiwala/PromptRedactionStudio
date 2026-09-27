# Sample documents

Fourteen small files for trying the **Document** tab by hand: drop them in, look at what
comes out, restore it, and open the results in Word or Excel. The automated tests do not use
them; `tests/office_fixtures.py` builds its own files in memory.

> **Every value here is made up.** Emails use `example.com`. Phone numbers are in the US
> 555-01xx and UK 020 7946 0xxx ranges set aside for fiction. Card numbers are the standard
> test numbers (`4111 1111 1111 1111`, `5555 5555 5555 4444`, `3782 822463 10005`). IBANs are
> the two from the IBAN documentation, SSNs are from the `987-65-432x` range reserved for
> advertising, and IP addresses are private (`192.168.x`). The names are fictional: Jane Doe,
> John Roe, Priya Placeholder, Marcus Testwell, Elena Sample, Tomas Fictitious, Sarah Dummy,
> Robert Test.

## What each file is for

"What happens" is what the app did with the web app's default settings (spaCy large,
threshold 0.35, the default entity list). Model results can shift between spaCy versions.

| File | What is in it | What happens |
|---|---|---|
| `01-customer-complaint.docx` | Name in the header, email in the footer, a name split across a bold and a plain run, a two-column table, an "email her" link whose address is an email, and author metadata | 11 items redacted in 9 places, including the header, footer and link address; author fields blanked. "Placeholder" stays readable (see below) |
| `02-hr-performance-review.docx` | Employee, manager, ID and salary in the body; a table of 5 team members; a Word comment naming someone | 20 items in 18 places, including the comment text; the comment's author is blanked |
| `03-medical-referral.docx` | Patient name, date of birth, address, NHS number, doctor and UK phone | 7 items, including the NHS number and both dates |
| `04-customer-list.xlsx` | Two sheets sharing names; two phone numbers stored as *numbers*; a card number in a text cell; a `SUM` formula; a cell comment naming someone; a bar chart | 48 items; numeric phones become tokens; the formula and chart are kept, with a "charts were not checked" warning |
| `05-payroll.xlsx` | Names, IBANs, SSNs and salaries | 21 items; every IBAN and SSN redacted |
| `06-invoice.pdf` | Two pages: billing details on page 1, card number and IBAN on page 2 | Returned as `06-invoice.redacted.txt`, with a warning that PDF layout is not kept; both pages redacted |
| `07-scanned-image-only.pdf` | Drawn shapes only, with no text layer, like a scan without OCR | Warning: no text was found, and OCR is not supported |
| `08-support-tickets.csv` | 12 tickets, UTF-8 with a byte order mark; each name appears in its own column and again in the message text | 46 items; the byte order mark is kept. Several names are missed entirely (see below) |
| `09-incident-postmortem.md` | Markdown table, engineer names, private IPs, an email, a UK phone and a URL | 12 items; the IPs are redacted. "Robert Test" is missed |
| `10-chat-transcript.txt` | A support chat with name, phone, email and street address | 6 items |
| `11-legacy-notes-cp1252.txt` | Windows-1252, not UTF-8: an en dash, curly quotes, and the name "José" | 8 items; comes back in Windows-1252 with the accents and quotes intact |
| `12-no-pii-control.docx` | A product description with no personal data | Nothing detected; the file is unchanged |
| `13-corrupt.docx` | Bytes that are not a zip, despite the extension | Refused: "Could not read that file as DOCX." |
| `14-unsupported.rtf` | An RTF file | Refused: "Unsupported file type .rtf" |

Every file that redacts also restores fully: dropping the redacted copy back into the
restore area returns all of its tokens.

## What the samples show the model missing

Every patterned value is redacted in every file: emails, phones, cards, IBANs, SSNs, the
NHS number and IP addresses. Names are harder, and some are missed on purpose. Placeholder,
Sample, Test and Fictitious are ordinary English words, so they are tougher for the name
model than most real surnames.

- **`08-support-tickets.csv`** is the one to try first. Elena Sample, Tomas Fictitious, John
  Roe, Priya Placeholder and Robert Test are never recognised in any row, so they stay
  readable. Open **Anything else to redact?** after redacting it: those names are near the
  top of the list. Hover one to see where it appears, tick it, and redact again. Names the
  model *did* catch somewhere, such as Marcus Testwell, are redacted in every row, including
  the `customer_name` column.
- **"Placeholder"** stays readable in `01`, `04` and `05`: the model finds "Priya" but not the
  surname.
- **Batches:** drop `01`, `04` and `08` in together. Priya is caught in `01` as `<PERSON_1>`,
  so she is `<PERSON_1>` in the other two as well, even where the model misses her there.

## Regenerating

`make_samples.py` rebuilds all fourteen files. It needs python-docx, openpyxl and reportlab,
which the app does not use, so install them in their own environment rather than the app's:

```powershell
python -m venv .venv-samples
.\.venv-samples\Scripts\pip install python-docx openpyxl reportlab
.\.venv-samples\Scripts\python samples\documents\make_samples.py
```

On macOS or Linux, use `.venv-samples/bin/` instead of `.venv-samples\Scripts\`.

Dates in the content are fixed, so a rerun rewrites the same documents rather than new ones.
PDFs, CSV, Markdown and text files come out byte-for-byte identical. Word and Excel files
differ only in zip timestamps and in the save time openpyxl always writes into an `.xlsx`.
