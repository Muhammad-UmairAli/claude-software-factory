# The factory's runtime container: Claude Code, git, gh, Python
# and openssl in a container that holds none of your machine's credentials, with
# outbound traffic limited to GitHub and the hosts in egress-allowlist.
#
# Build and enter it with factory/bin/runtime, not an editor's dev-container
# feature: VS Code's Dev Containers extension forwards your SSH agent, your git
# credentials and your ~/.gitconfig into a container by default.
# Node 24 is an LTS line (security fixes until 2028-04-30). Odd-numbered Node
# majors are never LTS and go out of support within months: move between LTS
# majors by hand; Dependabot doesn't propose major bumps here.
FROM node:24-bookworm-slim

# Claude Code at the version the guard hooks were last checked against
# (.github/claude-code-tested); factory/bin/runtime passes it in.
ARG CLAUDE_CODE_VERSION=2.1.292

# Debian packages from the base image's release.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl dnsutils git iproute2 ipset iptables less openssl procps python3 util-linux \
 && rm -rf /var/lib/apt/lists/*

# The GitHub CLI, from GitHub's signed apt repository.
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/*

# Newer npm skips install scripts unless a package is named; Claude Code's own
# installer (install.cjs) is the only one allowed to run.
RUN npm install -g --allow-scripts=@anthropic-ai/claude-code "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && npm cache clean --force

# The allowlist and the firewall belong to root and are baked into the image:
# nothing in the workspace can widen them, and the agents' user can't rerun them.
# The repository is bind-mounted from your machine, often under another uid;
# everything in this container is yours, so git may trust any directory.
COPY egress-allowlist /etc/factory/egress-allowlist
COPY init-firewall.sh /usr/local/sbin/factory-firewall
COPY entrypoint.sh /usr/local/sbin/factory-entrypoint
RUN chmod 0644 /etc/factory/egress-allowlist \
 && chmod 0755 /usr/local/sbin/factory-firewall /usr/local/sbin/factory-entrypoint \
 && mkdir -p /home/node/.claude /home/node/.config/gh /home/node/.config/software-factory \
 && chown -R node:node /home/node \
 && git config --system --add safe.directory '*'

# No auto-updates, telemetry or error reports from Claude Code: the firewall
# would block them anyway. Its login and settings live in the named volume at
# ~/.claude, so they survive the container (doctor reads the same place).
ENV CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    CLAUDE_CONFIG_DIR=/home/node/.claude
WORKDIR /workspace

# Starts as root only to install the firewall, then runs the command as `node`.
ENTRYPOINT ["/usr/local/sbin/factory-entrypoint"]
CMD ["bash"]
