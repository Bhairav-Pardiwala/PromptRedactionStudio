# About this folder

These files are the source of the project's **GitHub Wiki**, written for end users. They are
kept in the repository so they can be reviewed in pull requests alongside code changes; the
wiki itself has no review workflow.

Nothing in `app/`, `tray/` or `docs/` depends on them. `docs/GUIDE.md` remains the technical
reference — these pages link to it rather than duplicating it.

## Publishing to the wiki

A folder named `wiki/` in this repository is **not** the Wiki tab. GitHub keeps the wiki in
a separate repository, `PromptRedactionStudio.wiki.git`, and pushing here does nothing to
it.

**The first time**, that separate repository does not exist yet and cannot be cloned — the
clone fails with *Repository not found* even when the wiki is enabled in Settings →
Features. GitHub creates it when the first page is saved, and there is no API for that, so
it has to be done once by hand: open the repository's **Wiki** tab, choose *Create the first
page*, and save anything at all. The `Home.md` below replaces it.

**After that**, publishing is a copy:

```bash
git clone https://github.com/Bhairav-Pardiwala/PromptRedactionStudio.wiki.git
cp wiki/*.md PromptRedactionStudio.wiki/       # everything except this README
cd PromptRedactionStudio.wiki
rm -f README.md
git add -A && git commit -m "Update wiki" && git push
```

Nothing keeps the two in step automatically. Edit the pages here, review them in a pull
request, and re-run the copy — an edit made in GitHub's wiki editor will be overwritten by
the next publish.

## Two conventions to keep

- **`_Sidebar.md` and `_Footer.md` are magic filenames.** GitHub renders them on every wiki
  page. Other pages are addressed by filename with hyphens for spaces, which is why links
  read `[Using the Web App](Using-the-Web-App)` with no `.md`.
- **Image links are absolute `raw.githubusercontent.com` URLs on purpose.** The wiki is a
  separate repository, so a relative `docs/*.png` path resolves to nothing there. The
  trade-off is that the images only appear once the referenced branch is pushed.
