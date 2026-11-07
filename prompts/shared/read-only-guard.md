Never read `.env` files, private keys or credential folders, and never print
the environment; a guard hook blocks them. It also blocks any command that
changes git history or GitHub: you only read the branch and write your file.
