# syntax=docker/dockerfile:1.7
FROM ubuntu@sha256:008173c23f95b170204355c12626cb5a965d779a7e1283b09e9cffbb1bf33ca3 AS build-base

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        bison \
        build-essential \
        ca-certificates \
        cmake \
        flex \
        git \
        libdrm-dev \
        libelf-dev \
        libexpat1-dev \
        libglvnd-dev \
        libunwind-dev \
        libwayland-dev \
        libx11-dev \
        libx11-xcb-dev \
        libxcb-dri3-dev \
        libxcb-present-dev \
        libxcb-randr0-dev \
        libxcb-shm0-dev \
        libxcb-sync-dev \
        libxcb-xfixes0-dev \
        libxext-dev \
        libxfixes-dev \
        libxrandr-dev \
        libxshmfence-dev \
        libxxf86vm-dev \
        meson \
        ninja-build \
        pkg-config \
        python3 \
        python3-mako \
        python3-packaging \
        python3-pip \
        python3-ply \
        python3-yaml \
        zlib1g-dev \
    && python3 -m pip install --break-system-packages 'meson==1.7.2' \
    && find /var/lib/apt/lists -mindepth 1 -delete

FROM build-base AS llvm
COPY ci/venus-host.lock.json /venus-host.lock.json
RUN set -eux; \
    commit=$(python3 -c 'import json; print(json.load(open("/venus-host.lock.json"))["llvm"]["commit"])'); \
    git init -q /src/llvm-project; \
    git -C /src/llvm-project remote add origin https://github.com/llvm/llvm-project.git; \
    git -C /src/llvm-project fetch -q --depth 1 origin "$commit"; \
    git -C /src/llvm-project checkout -q --detach FETCH_HEAD; \
    test "$(git -C /src/llvm-project rev-parse HEAD)" = "$commit"; \
    cmake -S /src/llvm-project/llvm -B /src/llvm-build -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/opt/venus \
      -DLLVM_TARGETS_TO_BUILD=X86 \
      -DLLVM_ENABLE_PROJECTS= \
      -DLLVM_BUILD_LLVM_DYLIB=ON \
      '-DLLVM_DYLIB_COMPONENTS=bitwriter;mcdisassembler;mcjit;core;executionengine;target;passes;scalaropts;transformutils;instcombine;native' \
      -DLLVM_LINK_LLVM_DYLIB=ON \
      -DLLVM_ENABLE_RTTI=ON \
      -DLLVM_ENABLE_TERMINFO=OFF \
      -DLLVM_ENABLE_ZSTD=OFF \
      -DLLVM_INCLUDE_BENCHMARKS=OFF \
      -DLLVM_INCLUDE_EXAMPLES=OFF \
      -DLLVM_INCLUDE_TESTS=OFF \
      -DLLVM_INCLUDE_UTILS=OFF \
      '-DLLVM_DISTRIBUTION_COMPONENTS=LLVM;llvm-config;llvm-headers'; \
    ninja -C /src/llvm-build -j4 install-distribution; \
    test -x /opt/venus/bin/llvm-config; \
    test -e /opt/venus/lib/libLLVM.so; \
    test -f /opt/venus/include/llvm/Config/llvm-config.h

FROM build-base AS vulkan
COPY ci/venus-host.lock.json /venus-host.lock.json
RUN set -eux; \
    headers_commit=$(python3 -c 'import json; print(json.load(open("/venus-host.lock.json"))["vulkan_headers"]["commit"])'); \
    loader_commit=$(python3 -c 'import json; print(json.load(open("/venus-host.lock.json"))["vulkan_loader"]["commit"])'); \
    git init -q /src/Vulkan-Headers; \
    git -C /src/Vulkan-Headers remote add origin https://github.com/KhronosGroup/Vulkan-Headers.git; \
    git -C /src/Vulkan-Headers fetch -q --depth 1 origin "$headers_commit"; \
    git -C /src/Vulkan-Headers checkout -q --detach FETCH_HEAD; \
    test "$(git -C /src/Vulkan-Headers rev-parse HEAD)" = "$headers_commit"; \
    cmake -S /src/Vulkan-Headers -B /src/vulkan-headers-build -G Ninja \
      -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/venus; \
    ninja -C /src/vulkan-headers-build -j4 install; \
    git init -q /src/Vulkan-Loader; \
    git -C /src/Vulkan-Loader remote add origin https://github.com/KhronosGroup/Vulkan-Loader.git; \
    git -C /src/Vulkan-Loader fetch -q --depth 1 origin "$loader_commit"; \
    git -C /src/Vulkan-Loader checkout -q --detach FETCH_HEAD; \
    test "$(git -C /src/Vulkan-Loader rev-parse HEAD)" = "$loader_commit"; \
    cmake -S /src/Vulkan-Loader -B /src/vulkan-loader-build -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/opt/venus \
      -DVULKAN_HEADERS_INSTALL_DIR=/opt/venus \
      -DBUILD_TESTS=OFF \
      -DBUILD_WSI_DIRECTFB_SUPPORT=OFF \
      -DBUILD_WSI_WAYLAND_SUPPORT=OFF \
      -DBUILD_WSI_XCB_SUPPORT=OFF \
      -DBUILD_WSI_XLIB_SUPPORT=OFF \
      -DBUILD_WSI_XRANDR_SUPPORT=OFF; \
    ninja -C /src/vulkan-loader-build -j4 install

FROM build-base AS mesa
ARG MESA_CACHE_BUST=lock
RUN apt-get update \
    && apt-get install --yes --no-install-recommends glslang-tools \
    && find /var/lib/apt/lists -mindepth 1 -delete
COPY --chmod=0755 ci/venus-llvm-config /usr/local/bin/llvm-config
COPY ci/venus-host.lock.json /venus-host.lock.json
COPY --from=llvm /opt/venus /opt/venus
COPY --from=vulkan /opt/venus /opt/venus
ENV PATH=/usr/local/bin:/opt/venus/bin:$PATH \
    PKG_CONFIG_PATH=/opt/venus/lib/x86_64-linux-gnu/pkgconfig:/opt/venus/lib/pkgconfig \
    LD_LIBRARY_PATH=/opt/venus/lib/x86_64-linux-gnu:/opt/venus/lib
RUN set -eux; \
    test -n "$MESA_CACHE_BUST"; \
    commit=$(python3 -c 'import json; print(json.load(open("/venus-host.lock.json"))["mesa"]["commit"])'); \
    git init -q /src/mesa; \
    git -C /src/mesa remote add origin https://gitlab.freedesktop.org/mesa/mesa.git; \
    git -C /src/mesa fetch -q --depth 1 origin "$commit"; \
    git -C /src/mesa checkout -q --detach FETCH_HEAD; \
    test "$(git -C /src/mesa rev-parse HEAD)" = "$commit"; \
    meson setup /src/mesa-build /src/mesa \
      --buildtype=release \
      --prefix=/opt/venus \
      --wrap-mode=nodownload \
      -Dplatforms=[] \
      -Dgallium-drivers=llvmpipe \
      -Dvulkan-drivers=swrast \
      -Dllvm=enabled \
      -Dshared-llvm=enabled \
      -Dglx=disabled \
      -Degl=disabled \
      -Dgbm=disabled \
      -Dgles1=disabled \
      -Dgles2=disabled \
      -Dopengl=false \
      -Dbuild-tests=false \
      -Dvalgrind=disabled \
      -Dlibunwind=disabled; \
    ninja -C /src/mesa-build -j4 install

FROM mesa AS virglrenderer
ARG VIRGLRENDERER_CACHE_BUST=lock
RUN apt-get update \
    && apt-get install --yes --no-install-recommends libepoxy-dev \
    && find /var/lib/apt/lists -mindepth 1 -delete
COPY ci/venus-host.lock.json /venus-host.lock.json
RUN set -eux; \
    test -n "$VIRGLRENDERER_CACHE_BUST"; \
    commit=$(python3 -c 'import json; print(json.load(open("/venus-host.lock.json"))["virglrenderer"]["commit"])'); \
    git init -q /src/virglrenderer; \
    git -C /src/virglrenderer remote add origin https://gitlab.freedesktop.org/virgl/virglrenderer.git; \
    git -C /src/virglrenderer fetch -q --depth 1 origin "$commit"; \
    git -C /src/virglrenderer checkout -q --detach FETCH_HEAD; \
    test "$(git -C /src/virglrenderer rev-parse HEAD)" = "$commit"; \
    meson setup /src/virgl-build /src/virglrenderer \
      --buildtype=release \
      --prefix=/opt/venus \
      --wrap-mode=nodownload \
      -Dplatforms=[] \
      -Dvenus=true \
      -Dvulkan-dload=true \
      -Ddrm-renderers=[] \
      -Drender-server-worker=process \
      -Dvideo=false \
      -Dtests=false \
      -Dfuzzer=false \
      -Dtracing=none; \
    ninja -C /src/virgl-build -j4 install; \
    test -x /opt/venus/libexec/virgl_render_server; \
    test -f /opt/venus/share/vulkan/icd.d/lvp_icd.x86_64.json

FROM virglrenderer AS qemu
ARG QEMU_TSP_REF
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        libfdt-dev \
        libgbm-dev \
        libglib2.0-dev \
        libpixman-1-dev \
    && find /var/lib/apt/lists -mindepth 1 -delete
COPY UPSTREAM /stack/UPSTREAM
COPY pocketforge/ /stack/pocketforge/
COPY ci/venus-runtime-stage.py /stack/venus-runtime-stage.py
RUN set -eux; \
    test "${#QEMU_TSP_REF}" -eq 40; \
    case "$QEMU_TSP_REF" in *[!0-9a-f]*) exit 1 ;; esac; \
    repo=$(sed -n 's/^repo *= *//p' /stack/UPSTREAM); \
    commit=$(sed -n 's/^commit *= *//p' /stack/UPSTREAM); \
    git init -q /src/qemu; \
    git -C /src/qemu remote add origin "$repo"; \
    git -C /src/qemu fetch -q --depth 1 origin "$commit"; \
    git -C /src/qemu checkout -q --detach FETCH_HEAD; \
    test "$(git -C /src/qemu rev-parse HEAD)" = "$commit"; \
    GIT_COMMITTER_NAME=PocketForge \
    GIT_COMMITTER_EMAIL=build@pocketforge.invalid \
      git -C /src/qemu am /stack/pocketforge/*.patch; \
    mkdir /src/qemu/build; \
    cd /src/qemu/build; \
    PKG_CONFIG_PATH=/opt/venus/lib/x86_64-linux-gnu/pkgconfig:/opt/venus/lib/pkgconfig \
    LD_LIBRARY_PATH=/opt/venus/lib/x86_64-linux-gnu:/opt/venus/lib \
      ../configure \
        --target-list=aarch64-softmmu \
        --without-default-features \
        --with-pkgversion="pocketforge-qemu-tsp-head=$QEMU_TSP_REF" \
        -Dpixman=enabled \
        -Dgio=enabled \
        -Ddbus_display=enabled \
        --enable-opengl \
        --enable-virglrenderer; \
    LD_LIBRARY_PATH=/opt/venus/lib/x86_64-linux-gnu:/opt/venus/lib \
      ninja -j4 qemu-system-aarch64; \
    LD_LIBRARY_PATH=/opt/venus/lib/x86_64-linux-gnu:/opt/venus/lib \
      python3 /stack/venus-runtime-stage.py \
        --qemu /src/qemu/build/qemu-system-aarch64 \
        --render-server /opt/venus/libexec/virgl_render_server \
        --virglrenderer /opt/venus/lib/x86_64-linux-gnu/libvirglrenderer.so.1.11.0 \
        --vulkan-loader /opt/venus/lib/libvulkan.so.1.4.365 \
        --lavapipe /opt/venus/lib/x86_64-linux-gnu/libvulkan_lvp.so \
        --llvm /opt/venus/lib/libLLVM.so.20.1 \
        --icd /opt/venus/share/vulkan/icd.d/lvp_icd.x86_64.json \
        --lock /venus-host.lock.json \
        --output /runtime; \
    /runtime/qemu-system-aarch64 -M help | grep -q pocketforge-a133

FROM scratch AS export
COPY --from=qemu /runtime/ /
