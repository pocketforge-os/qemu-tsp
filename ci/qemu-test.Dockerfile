FROM ubuntu@sha256:008173c23f95b170204355c12626cb5a965d779a7e1283b09e9cffbb1bf33ca3

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        bison \
        build-essential \
        ca-certificates \
        flex \
        git \
        libfdt-dev \
        libglib2.0-dev \
        libpixman-1-dev \
        meson \
        ninja-build \
        pkg-config \
        python3 \
        python3-venv \
        zlib1g-dev \
    && find /var/lib/apt/lists -mindepth 1 -delete
