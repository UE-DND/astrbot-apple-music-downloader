#!/bin/bash

CONFIG_SRC="/app/config.yaml"
if [ -f /app/config-host.yaml ]; then
    CONFIG_SRC="/app/config-host.yaml"
fi

if [ ! -f "$CONFIG_SRC" ]; then
    echo "未找到配置文件 ($CONFIG_SRC)" >&2
    exit 1
fi

CONFIG_RUNTIME="/tmp/config.yaml"
cp "$CONFIG_SRC" "$CONFIG_RUNTIME"

WRAPPER_HOST="${WRAPPER_HOST:-apple-music-wrapper}"

sed -Ei "s#^(decrypt-m3u8-port:\\s*\\\"?)[^\\\"]+(\\\"?)#\\1${WRAPPER_HOST}:10020\\2#" "$CONFIG_RUNTIME"
sed -Ei "s#^(get-m3u8-port:\\s*\\\"?)[^\\\"]+(\\\"?)#\\1${WRAPPER_HOST}:20020\\2#" "$CONFIG_RUNTIME"

export AMDL_CONFIG_PATH="$CONFIG_RUNTIME"

cd /app
exec /usr/local/bin/apple-music-downloader "$@"
