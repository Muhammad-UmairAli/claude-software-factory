Never read `.env` files, private keys or credential folders, and never print
the environment; a guard hook blocks them. Your shell is read-only: search,
`cat`, `git log`/`show`/`diff` inside the repository, nothing that runs a
program or changes git or GitHub. You only read the branch and write your file.
