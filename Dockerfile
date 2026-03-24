# Dockerfile for bugeval-agent-v2: Rust toolchain + Claude Code CLI.
# Tools enabled: Bash (cargo, clippy, rg), Read, Glob, Grep, WebSearch
# Network: full outbound (API access, web search, crate downloads)
#
# Auth: Use a Docker named volume for Claude Code auth:
#   docker run -it -v bugeval-claude-auth:/home/agent/.claude \
#     -e CLAUDE_CONFIG_DIR=/home/agent/.claude \
#     bugeval-agent-v2 claude /login
FROM rust:1.82-slim

# System tools + Node.js + Python for Claude Code CLI + Agent SDK
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ripgrep \
    jq \
    ca-certificates \
    nodejs \
    npm \
    python3 \
    python3-pip \
  && rm -rf /var/lib/apt/lists/*

# Rust components for code analysis
RUN rustup component add clippy rustfmt

# Install Claude Code CLI globally
RUN npm install -g @anthropic-ai/claude-code@1.0.3

# Install Agent SDK for Python
RUN pip3 install --break-system-packages claude-agent-sdk==0.0.14

# Run as non-root; /home/agent for config, /work for workspace
RUN groupadd -r agent && useradd -r -g agent -m -d /home/agent -s /bin/bash agent
RUN mkdir -p /work /home/agent/.claude && chown -R agent:agent /work /home/agent

ENV HOME=/home/agent
ENV CLAUDE_CONFIG_DIR=/home/agent/.claude

USER agent
WORKDIR /work
