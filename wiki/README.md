# About this folder

These files are the source of the project's **GitHub Wiki**, written for end users. They are
kept in the repository so they can be reviewed in pull requests alongside code changes; the
wiki itself has no review workflow.

Nothing in `app/`, `tray/` or `docs/` depends on them. `docs/GUIDE.md` remains the technical
reference — these pages link to it rather than duplicating it.

## Publishing to the wiki

```bash
git clone https://github.com/Bhairav-Pardiwala/PromptRedactionStudio.wiki.git
cp wiki/*.md PromptRedactionStudio.wiki/       # everything except this README
cd PromptRedactionStudio.wiki
rm -f README.md
git add -A && git commit -m "Update wiki" && git push
```

The wiki has to be enabled once in the repository's Settings → Features before that clone
URL exists.

## Two conventions to keep

- **`_Sidebar.md` and `_Footer.md` are magic filenames.** GitHub renders them on every wiki
  page. Other pages are addressed by filename with hyphens for spaces, which is why links
  read `[Using the Web App](Using-the-Web-App)` with no `.md`.
- **Image links are absolute `raw.githubusercontent.com` URLs on purpose.** The wiki is a
  separate repository, so a relative `docs/*.png` path resolves to nothing there. The
  trade-off is that the images only appear once the referenced branch is pushed.
