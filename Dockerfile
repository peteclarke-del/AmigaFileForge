FROM python:3.14-slim-trixie AS python-deps

# PyPI does not publish Capstone binaries for every Linux architecture. In
# particular, 32-bit Raspberry Pi builds fall back to the source distribution,
# which needs a native compiler and make. Install into a disposable root rather
# than carrying locally tagged wheels into the runtime stage. This avoids a
# second architecture-tag compatibility decision after the native package has
# already built successfully. The final image remains free of compilers and
# headers.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY requirements.txt .
RUN python -m pip install --no-cache-dir --root=/python-install -r requirements.txt \
    && PYTHONPATH="$(python -c 'import sysconfig; print("/python-install" + sysconfig.get_path("purelib"))')" \
       python -c "from capstone import CS_ARCH_M68K, CS_MODE_M68K_000, CS_MODE_M68K_020, CS_MODE_M68K_040, Cs; Cs(CS_ARCH_M68K, CS_MODE_M68K_000); print('Staged Capstone 68000/68020/68040 support is available')"

FROM debian:bookworm-slim AS hxc-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git make gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
COPY tools/build-hxc-runtime.sh /usr/local/bin/build-hxc-runtime
RUN build-hxc-runtime /opt/hxc

FROM python:3.14-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# fs-uae is the reference Amiga emulator for the hand-off feature. It needs a
# Kickstart ROM the user supplies; none is shipped, because Kickstart is not
# redistributable.
#
# Only the emulator itself is installed. fs-uae-launcher is its GTK front end,
# it was dropped from Debian after bookworm, and nothing here invokes it: the
# workbench builds an fs-uae command line directly.
RUN apt-get update && apt-get install -y --no-install-recommends \
    fs-uae xvfb xauth x11vnc novnc websockify imagemagick xdotool \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python-deps /python-install/usr/local /usr/local
RUN python -c "from capstone import CS_ARCH_M68K, CS_MODE_M68K_000, Cs; Cs(CS_ARCH_M68K, CS_MODE_M68K_000); print('Capstone 68000 support is available')"

COPY --from=hxc-builder /opt/hxc/bin/hxcfe /usr/local/bin/hxcfe
COPY --from=hxc-builder /opt/hxc/lib/libhxcfe.so /usr/local/lib/libhxcfe.so
COPY --from=hxc-builder /opt/hxc/lib/libusbhxcfe.so /usr/local/lib/libusbhxcfe.so
RUN ldconfig

COPY VERSION ./VERSION
COPY app ./app
COPY amiganut ./amiganut
COPY amiga_floppy ./amiga_floppy
COPY amiga_greaseweazle ./amiga_greaseweazle
COPY firmware ./firmware

# The bundled engine is importable and provides the `adisc` command line the
# workbench shells out to for bulk operations.
RUN python -c "import amiganut; from amiganut.filesystem import list_filesystems; print('Amiganut', amiganut.__version__, [row['name'] for row in list_filesystems()])"

RUN mkdir -p /app/work

EXPOSE 8666 8668

CMD ["gunicorn", "--bind", "0.0.0.0:8666", "--workers", "1", "--threads", "8", "--timeout", "300", "--access-logfile", "-", "app.wsgi:app"]
