# Second brain

Living memory for this project. Business requirements, business knowledge and
decisions are recorded here as they are given, so the project keeps improving
without anything having to be repeated.

How it is used:

- **Claude reads this file first** at the start of every session, and updates it
  in the same change whenever new requirements or business knowledge are given
  (see `CLAUDE.md`).
- **The app reads the "Business words" table** below when it starts, so the
  chatbot understands the business's own vocabulary.

Last updated: 2026-11-15

## Purpose

A private analytics workspace for people who are not analysts. Drop in a file,
confirm what was found, then ask questions, build tables and charts, and run
predictions. The two things it must protect are **confidentiality** (data never
leaves the user's computer) and **accuracy** (answers come only from the
uploaded files, and every answer can be audited).

## Business words

The app loads this table. Each row teaches the chatbot that a word the business
uses means a field name or a value in the uploaded data. Add rows as vocabulary
comes up. Rows whose first cell starts with `_` are ignored.

| Business word | Means (field or value in the data) | Notes |
|---|---|---|
| revenue | Sales Amount | Sample data vocabulary |
| turnover | Sales Amount | |
| takings | Sales Amount | |
| units | Quantity | |
| client | Customer ID | |
| satisfaction | Satisfaction Score | |
| tickets | Support Tickets | |
| _example | _Field Name_ | _Copy this row to add a word_ |

## Business requirements

Recorded from the project owner. Newest entries at the bottom of each group.

### Experience
- R1. Live, interactive web interface. No terminal, no installation.
- R2. First screen invites "Drop a file to begin". Accepts Excel, Word,
  PowerPoint and similar documents.
- R3. The app reads the file, shows a preview, and the user confirms the import.
- R4. Must be friendly for non-technical users. It must not feel like an
  analytics tool that needs analytics knowledge to navigate.
- R5. Users never read or type SQL. Everything is drag and drop or plain words.
- R6. Professional, minimal, intuitive look.

### Data handling
- R7. A SQL backend holds the uploaded data.
- R8. On upload the system suggests fields to join tables on, with data match %
  and a confidence level, and a one-click accept.
- R9. On upload the system reports missing data, duplicated data, anomalies and
  abnormalities, and inconsistencies, each with a one-click accept.
- R10. Confidentiality: data stays private. No external data is brought in.
- R11. Accuracy: answers and displays use only the uploaded documents.

### Three connected tabs
- R12. **Chatbot.** Answers questions from the uploaded documents using
  deterministic Python. No LLM. Matches the words in the question to the words
  in the documents. Every answer has a "How I got this" so it can be audited.
- R13. **Table and chart builder.** Drag and drop fields from the uploaded data.
  Export the table and the chart.
- R14. **Machine learning.** Forecasts, time series, prediction and similar.
  Results (model accuracy, feature importance, confidence bands and so on) are
  explained in plain English.
- R15. The tabs are connected: a chat answer has "Open in builder", which leads
  to the table and chart; "Ask about this" connects to machine learning results.

### Project
- R16. Code lives in the connected GitHub repository.
- R17. This file acts as the second brain and is updated continuously.
- R18. Dummy data is included to test all three tabs.

## Business knowledge

Facts about the business, its data and its rules. Nothing recorded yet beyond
the sample data. Add things like: what each file contains, how figures are
defined (for example "revenue excludes refunds"), reporting periods, thresholds
that matter, who uses the output.

## Decisions

| Date | Decision | Why |
|---|---|---|
| 2026-11-15 | Everything runs in the browser (Python via Pyodide, SQL via SQLite). No server. | Owner's choice. Uploaded files never leave the computer, and there is nothing to host or pay for. |
| 2026-11-15 | Published with GitHub Pages. | Owner's choice. A normal web link, no terminal. |
| 2026-11-15 | The second brain both records notes and teaches the chatbot vocabulary. | Owner's choice. |
| 2026-11-15 | Data lives in memory only. Closing or reloading the tab erases it. | Confidentiality. Nothing is written to disk or sent anywhere. |
| 2026-11-15 | Data fixes change a working copy, never the original import. "Undo all changes" restores it. | Accuracy and auditability. |
| 2026-11-15 | The chatbot refuses rather than guesses when words do not match the files. | Accuracy. |
| 2026-11-15 | SQL is hidden behind a "Technical detail" disclosure inside "How I got this". | Users never need SQL, but an analyst can still verify an answer. |
| 2026-11-15 | Charts follow one consistent style; no dual axes; at most 8 colours. | Readability for non-analysts. |

## Open questions

- Should imported data be remembered between visits (stored in the browser), or
  always start fresh? Currently always fresh.
- Largest file size the business expects? The browser engine is comfortable up
  to roughly a few hundred thousand rows.
- Are there fixed business definitions (fiscal year start, currency, what counts
  as an active customer) that the chatbot should apply automatically?

## Ideas not yet built

- Saving and reopening a workspace.
- Follow-up questions that remember the previous one ("and for South?").
- Joining on two fields at once.
- Scanned PDFs (text recognition).

## Change log

| Date | Change |
|---|---|
| 2026-11-15 | First version: file import with preview, data check with one-click fixes, join suggestions, chatbot, table and chart builder, machine learning tab, sample data, this file. |
