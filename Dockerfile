FROM python:3.13-slim AS build

WORKDIR /src

RUN pip install --no-cache-dir build

COPY pyproject.toml README.md ./
COPY *.py ./
COPY static ./static
COPY templates ./templates
COPY man ./man

RUN python -m build --wheel --outdir /wheels

FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DISPLAY=:99 \
    LOCATION_TRACKER_PORT=7070

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        fluxbox \
        netcat-openbsd \
        novnc \
        socat \
        websockify \
        x11vnc \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /wheels /wheels
COPY --from=build /src/static /tmp/location-tracker-static
COPY --from=build /src/templates /tmp/location-tracker-templates

RUN python -m pip install --no-cache-dir /wheels/*.whl \
    && python -c "from pathlib import Path; import shutil, dashboard; root = Path(dashboard.__file__).parent; shutil.copytree('/tmp/location-tracker-static', root / 'static', dirs_exist_ok=True); shutil.copytree('/tmp/location-tracker-templates', root / 'templates', dirs_exist_ok=True)" \
    && python -m playwright install --with-deps chromium \
    && rm -rf /wheels /tmp/location-tracker-static /tmp/location-tracker-templates

RUN useradd --create-home --shell /bin/sh location
RUN mkdir -p /home/location/.local/share/location-tracker /home/location/.config/location-tracker \
    && chown -R location:location /home/location /ms-playwright

RUN printf '%s\n' \
    '#!/bin/sh' \
    'set -eu' \
    'key_file="${LOCATION_TRACKER_KEY_FILE:-$HOME/.local/share/location-tracker/cookie-encryption.key}"' \
    'case "$1" in' \
    '  find-generic-password)' \
    '    if [ -n "${LOCATION_TRACKER_FERNET_KEY:-}" ]; then' \
    '      printf "%s\n" "$LOCATION_TRACKER_FERNET_KEY"' \
    '      exit 0' \
    '    fi' \
    '    if [ -s "$key_file" ]; then' \
    '      cat "$key_file"' \
    '      exit 0' \
    '    fi' \
    '    exit 44' \
    '    ;;' \
    '  add-generic-password)' \
    '    key=""' \
    '    while [ "$#" -gt 0 ]; do' \
    '      if [ "$1" = "-w" ]; then' \
    '        shift' \
    '        key="${1:-}"' \
    '        break' \
    '      fi' \
    '      shift' \
    '    done' \
    '    [ -n "$key" ] || exit 64' \
    '    mkdir -p "$(dirname "$key_file")"' \
    '    umask 077' \
    '    printf "%s\n" "$key" > "$key_file"' \
    '    exit 0' \
    '    ;;' \
    'esac' \
    'echo "unsupported security command: $*" >&2' \
    'exit 64' \
    > /usr/local/bin/security \
    && chmod +x /usr/local/bin/security

USER location
WORKDIR /home/location

EXPOSE 7070 7071 5900 6080

CMD ["location-tracker", "_serve"]
