# Plainly

A private analytics workspace for people who are not analysts. Drop in a file, confirm
what was found, then ask questions, build tables and charts, and run predictions.

Everything runs inside the browser tab. Uploaded files are never sent anywhere.

## Open the app

Turn on GitHub Pages once (no terminal needed):

1. On GitHub, open this repository, then **Settings**, then **Pages**.
2. Under **Build and deployment**, set **Source** to **Deploy from a branch**.
3. Choose the branch that holds this code and the folder **/ (root)**, then **Save**.

After a minute the page shows your link, in the form
`https://<your-username>.github.io/analyticschatbotv1/`. Every later change to that
branch is published automatically.

On the free GitHub plan, Pages needs the repository to be public. That makes the code
public, not your data: your data is never in the repository.

## Try it

Select **Try it with sample data** on the first screen. It loads five invented files
(orders, customers, products, a Word review and a PowerPoint deck) that contain
deliberate mistakes for the data check to find.

## What is inside

| Screen | What it does |
|---|---|
| Drop a file | Reads Excel, CSV, Word, PowerPoint, PDF, text. Shows a preview; you confirm. |
| Your data | Finds blanks, repeats, inconsistent spellings, extreme values and bad dates, each with a one-click fix. Suggests which tables to link, with match % and confidence. |
| Ask | Answers from your files by matching your words to theirs. No AI language model. Every answer has "How I got this". |
| Tables & charts | Drag fields into boxes. Export the table (Excel, CSV) and chart (PNG). |
| Predictions | Forecasts, what influences a result, natural groups, unusual records. Plain-English results with reliability. |

`SECOND_BRAIN.md` holds the business requirements and knowledge for the project, and
its "Business words" table teaches the chatbot your vocabulary.

## Privacy

- Python (Pyodide) and the SQL database (SQLite) run in your browser.
- The only downloads are the app's own files and the Pyodide engine from cdn.jsdelivr.net.
- Data is held in memory. Closing or reloading the tab erases it.
