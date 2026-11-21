---
name: qa-shot
description: Load before you screenshot, view, check or validate any web page (login, form, layout) or a UI change. Takes real screenshots of the running app to look at.
metadata:
  category: Quality
---
# qa-shot

One script, `scripts/shot.mjs`: it opens pages, runs steps with a real mouse and keyboard, and writes every shot into one labelled PNG. Judge the UI from that image, not from code.

## Get it on the machine
- Loading this skill copies the script to the machine once the user allows it. The load result names the folder: `SHOT=<folder>/scripts/shot.mjs`. Never retype the script.
- Needs Node 22 or newer (`node -v`) and Chromium. If `node $SHOT --help` fails with "no Chromium found", ask before installing: `npx playwright install chromium-headless-shell`, or set `CHROME=/path/to/chrome`.
- The app must be running. Use the project's dev server; ask before starting one.

## Use
```bash
node $SHOT --help                                              # every option
node $SHOT --base http://localhost:3000                        # the home page
node $SHOT --base URL --page /,/pricing --size 1440x900,390x844 --touch   # pages x desktop/phone, one image
node $SHOT --base URL --find "Sign in"                         # the selector of whatever shows that text
node $SHOT --base URL --click 'button.menu' --shot '.menu-list' --pad 20  # open, then shoot one element
node $SHOT --base URL --static /assets/=./dist,/assets/=../main/dist --shot header   # two builds side by side
node $SHOT --base URL --eval 'document.querySelectorAll("li").length'   # a measurement, no image
```
- It prints the PNG path (under `/tmp/qa-shot/`, cleared after an hour). Open it with `read_file` and look before you say anything about the UI.
- Steps run in the order given: `--find --click --hover --type --key --wait --eval --shot`. A click on something covered fails and names what is on top. A selector that matches nothing visible prints the closest names on the page.
- `--find TEXT` first when you do not know a selector; never guess one from templates.
- A comma list in `--page`, `--size` or `--static` repeats the steps per combination, all cells in one image.

## Sign-in
- `--cookies FILE`: a JSON array of cookies set before the first page.
- `--login /login` with `SHOT_USER`/`SHOT_PASS` in the environment fills and submits the form when the page lands there; with `--cookies FILE` the session is saved and reused. Ask the user for a test login; never use their own, and never print the password.

## Safety
- Every request but GET/HEAD/OPTIONS is aborted, so runs change nothing and can run in parallel. `--allow-writes` lets writes through: only on a dev instance, only when the user agrees.
- Point it only at apps the user named.

## App-specific setup
For views, fake states or a custom sign-in, write a `--setup app-shot.mjs` module in the project (hooks are listed above `main()` in the script) and keep a project wrapper that runs `main(argv, setup)`.
