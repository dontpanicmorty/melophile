---
name: git-identity
description: The author identity every commit in this repository must use, and what never goes into commit history. Use this before any git commit, amend, rebase, merge or history rewrite in this repository.
---

# Git identity for this repository

This repository is public, so its commit history is public too.

## Author email: always the private GitHub address

Every commit must be authored and committed with:

```
62984107+dontpanicmorty@users.noreply.github.com
```

Never use the owner's personal email address in a commit, as author or committer.

- The local git config sets it already: `git config user.email` here returns the address above.
- Check it before committing, especially in a fresh clone, which doesn't copy the local config:
  `git config user.email 62984107+dontpanicmorty@users.noreply.github.com`.
- If a commit is made with any other email, amend or rewrite it before pushing.

## Never in commit history

- No `Co-Authored-By` trailers, and no lines crediting an AI tool. The README already says agentic AI was
  used to build the app.
- No employer names, internal ticket or workspace identifiers, or links to private systems.
- No private machine details: no home-folder paths, user names, host names, or anything from a real music
  library. Tests and examples use made-up paths such as `/path/to/music`.
