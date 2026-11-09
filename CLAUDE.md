# Rules for this repository

1. **Never stage, commit or push on `main`** once it exists on GitHub. Work on a branch.
2. **Never stage, commit or push without the owner's explicit confirmation** for that specific action, in
   that conversation. An approval for one commit or push doesn't carry over to the next.
3. **Commit identity:** follow `.claude/skills/git-identity/SKILL.md`. Use only the noreply email. Add no
   AI co-author trailers or credits in commits.
4. **No Homebrew.** Install tools from the vendor's own download or installer (uv from Astral's installer,
   fpcalc from Chromaprint's GitHub releases).
5. **Never touch the real library in tests or the self-test.** They make their own audio in a temporary folder,
   set `MELOPHILE_HOME`, and never move files to the real Trash or contact the Music app.
