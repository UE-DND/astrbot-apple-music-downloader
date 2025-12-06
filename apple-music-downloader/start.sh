#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WRAPPER_DIR="${ROOT_DIR}/wrapper"
DOWNLOADS_DIR="${ROOT_DIR}/AM-DL downloads"
CONFIG_FILE="${ROOT_DIR}/config.yaml"
CREDENTIAL_DIR="${WRAPPER_DIR}/rootfs/data/data/com.apple.android.music/files"

WRAPPER_IMAGE="apple-music-wrapper"
WRAPPER_CONTAINER="apple-music-wrapper"
DOWNLOADER_IMAGE="apple-music-downloader"
DOCKER_NETWORK="apple-music-net"

DECRYPT_PORT="${DECRYPT_PORT:-10020}"
M3U8_PORT="${M3U8_PORT:-20020}"

COLOR_GREEN="\033[32m"
COLOR_RED="\033[31m"
COLOR_YELLOW="\033[33m"
COLOR_CYAN="\033[36m"
COLOR_RESET="\033[0m"

NON_INTERACTIVE="${AMDL_NON_INTERACTIVE:-0}"
USE_SAVED_ONLY="${AMDL_USE_SAVED:-0}"

info()    { printf "%b[i]%b %s\n" "$COLOR_CYAN" "$COLOR_RESET" "$*"; }
warn()    { printf "%b[!]%b %s\n" "$COLOR_YELLOW" "$COLOR_RESET" "$*"; }
success() { printf "%b[✓]%b %s\n" "$COLOR_GREEN" "$COLOR_RESET" "$*"; }
error()   { printf "%b[x]%b %s\n" "$COLOR_RED" "$COLOR_RESET" "$*" 1>&2; }

usage() {
  cat <<'EOF'
用法：
  ./start.sh [全局参数] [命令] [参数]

全局参数：
  --non-interactive | -y    无交互模式，默认选 Yes，缺少凭证时直接报错
  --use-saved                强制只使用已保存凭证，若不存在则失败

可用命令：
  start                 启动 Wrapper 服务（如需登录会引导）
  stop                  停止 Wrapper 服务
  download <链接>       下载音乐，支持附加参数：
                          --song --atmos --aac --select --debug --all-album
  status                查看服务状态
  logs                  查看 Wrapper 日志（最近 50 行）
  clean                 清理 Docker 资源
  help                  显示帮助

无参数运行将进入交互菜单。
EOF
}

ensure_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    error "未找到 docker，请先安装 Docker。"
    return 1
  fi

  if ! docker info >/dev/null 2>&1; then
    error "Docker 未运行或当前用户无权限。请启动 Docker 并确保可直接运行 docker 命令。"
    return 1
  fi
}

build_wrapper_image() {
  if [[ -n "$(docker images -q "${WRAPPER_IMAGE}")" ]]; then
    return 0
  fi

  info "未找到 ${WRAPPER_IMAGE} 镜像，开始构建（首次可能耗时较长）..."
  docker build --tag "${WRAPPER_IMAGE}" "${WRAPPER_DIR}"
  success "Wrapper 镜像构建完成"
}

build_downloader_image() {
  if [[ -n "$(docker images -q "${DOWNLOADER_IMAGE}")" ]]; then
    return 0
  fi

  info "未找到 ${DOWNLOADER_IMAGE} 镜像，开始构建（Go + FFmpeg 依赖，首次需数分钟）..."
  docker build -f "${ROOT_DIR}/Dockerfile.downloader" -t "${DOWNLOADER_IMAGE}" "${ROOT_DIR}"
  success "下载器镜像构建完成"
}

ensure_paths() {
  mkdir -p "${DOWNLOADS_DIR}"
  mkdir -p "${CREDENTIAL_DIR}"
}

ensure_network() {
  if ! docker network ls --format '{{.Name}}' | grep -q "^${DOCKER_NETWORK}$"; then
    info "创建 Docker 网络 ${DOCKER_NETWORK}"
    docker network create "${DOCKER_NETWORK}" >/dev/null
  fi
}

check_config() {
  if [[ ! -f "${CONFIG_FILE}" ]]; then
    error "未找到配置文件：${CONFIG_FILE}。请先创建 config.yaml。"
    exit 1
  fi
}

wrapper_running() {
  docker ps --format '{{.Names}}' | grep -q "^${WRAPPER_CONTAINER}$"
}

show_status() {
  ensure_docker || return 1
  echo ""
  echo "=== Apple Music Downloader 状态 ==="
  docker ps --filter "name=${WRAPPER_CONTAINER}" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" || true

  if wrapper_running; then
    if docker logs "${WRAPPER_CONTAINER}" 2>/dev/null | grep -q "listening"; then
      success "Wrapper 服务运行中，端口 ${DECRYPT_PORT}/${M3U8_PORT}"
    else
      warn "Wrapper 已启动，但监听状态未知，建议查看日志。"
    fi
  else
    warn "Wrapper 未运行，可执行 ./start.sh start 启动"
  fi
  echo ""
}

show_logs() {
  ensure_docker || return 1
  if ! docker ps -a --format '{{.Names}}' | grep -q "^${WRAPPER_CONTAINER}$"; then
    warn "未找到 ${WRAPPER_CONTAINER} 容器"
    return 0
  fi
  info "显示最近 50 行日志"
  docker logs --tail 50 "${WRAPPER_CONTAINER}"
}

clean_resources() {
  ensure_docker || return 1
  echo ""
  echo "清理选项："
  echo " 1) 停止容器但保留镜像（推荐）"
  echo " 2) 删除容器和镜像"
  echo " 3) 完全清理（含构建缓存）"
  echo " 0) 返回上一级"
  read -rp "选择 [0-3]: " choice

  case "$choice" in
    0)
      return 0
      ;;
    1)
      docker stop "${WRAPPER_CONTAINER}" >/dev/null 2>&1 || true
      docker rm "${WRAPPER_CONTAINER}"   >/dev/null 2>&1 || true
      success "容器已清理，镜像已保留"
      ;;
    2)
      docker stop "${WRAPPER_CONTAINER}" >/dev/null 2>&1 || true
      docker rm "${WRAPPER_CONTAINER}"   >/dev/null 2>&1 || true
      docker rmi "${WRAPPER_IMAGE}" "${DOWNLOADER_IMAGE}" >/dev/null 2>&1 || true
      success "容器与镜像已删除"
      ;;
    3)
      docker stop "${WRAPPER_CONTAINER}" >/dev/null 2>&1 || true
      docker rm "${WRAPPER_CONTAINER}"   >/dev/null 2>&1 || true
      docker rmi "${WRAPPER_IMAGE}" "${DOWNLOADER_IMAGE}" >/dev/null 2>&1 || true
      docker builder prune -f >/dev/null 2>&1 || true
      success "已完成深度清理"
      ;;
    *)
      warn "无效选择"
      ;;
  esac
}

start_services() {
  ensure_docker || return 1
  ensure_paths
  build_wrapper_image
  build_downloader_image
  ensure_network

  docker rm -f "${WRAPPER_CONTAINER}" >/dev/null 2>&1 || true

  local has_credentials="false"
  if [[ -d "${CREDENTIAL_DIR}" ]] && [[ -n "$(find "${CREDENTIAL_DIR}" -type f ! -name '.gitkeep' 2>/dev/null | head -n1)" ]]; then
    has_credentials="true"
  fi

  if [[ "${NON_INTERACTIVE}" -eq 1 ]]; then
    if [[ "${has_credentials}" != "true" ]]; then
      error "缺少已保存的 Apple Music 凭证，且处于无交互模式，无法登录。请在交互模式下运行 ./start.sh start 完成首次登录。"
      return 1
    fi
  else
    read -rp "是否登录 Apple Music? [Y/n] " login_choice
    if [[ -n "${login_choice}" && ! "${login_choice}" =~ ^[Yy]$ ]]; then
      warn "已取消启动"
      return 0
    fi
  fi

  local need_interactive="false"

  if [[ "${has_credentials}" == "true" ]]; then
    if [[ "${NON_INTERACTIVE}" -eq 1 || "${USE_SAVED_ONLY}" -eq 1 ]]; then
      info "使用已保存的凭证"
      need_interactive="false"
    else
      read -rp "检测到本地凭证，是否直接使用? [Y/n] " use_saved
      if [[ -n "${use_saved}" && ! "${use_saved}" =~ ^[Yy]$ ]]; then
        info "清理旧凭证..."
        rm -rf "${CREDENTIAL_DIR:?}"/* 2>/dev/null || true
        need_interactive="true"
      else
        info "使用已保存的凭证"
        need_interactive="false"
      fi
    fi
  else
    if [[ "${USE_SAVED_ONLY}" -eq 1 ]]; then
      error "未找到凭证，且指定只使用已保存凭证。请先交互登录一次。"
      return 1
    fi
    need_interactive="true"
  fi

  if [[ "${need_interactive}" == "true" ]]; then
    read -rp "Apple ID: " apple_id
    read -srp "密码: " apple_pwd; echo ""
    local login_args="-L ${apple_id}:${apple_pwd} -H 0.0.0.0"

    info "启动交互式登录容器（输入验证码后看到 listening 提示，按 Ctrl+C 结束以继续）..."
    docker run --rm -it --name "${WRAPPER_CONTAINER}" \
      -v "${WRAPPER_DIR}/rootfs/data:/app/rootfs/data" \
      -p "${DECRYPT_PORT}:10020" \
      -p "${M3U8_PORT}:20020" \
      --network "${DOCKER_NETWORK}" \
      -e "args=${login_args}" \
      -e "WRAPPER_HOST=${WRAPPER_CONTAINER}" \
      "${WRAPPER_IMAGE}"
    info "交互式登录结束，使用保存的凭证后台启动..."
  fi

  docker rm -f "${WRAPPER_CONTAINER}" >/dev/null 2>&1 || true

  docker run -d --name "${WRAPPER_CONTAINER}" \
    -v "${WRAPPER_DIR}/rootfs/data:/app/rootfs/data" \
    -p "${DECRYPT_PORT}:10020" \
    -p "${M3U8_PORT}:20020" \
    --network "${DOCKER_NETWORK}" \
    -e "args=-H 0.0.0.0" \
    -e "WRAPPER_HOST=${WRAPPER_CONTAINER}" \
    "${WRAPPER_IMAGE}" >/dev/null

  sleep 3
  if wrapper_running; then
    success "Wrapper 已启动（解密端口: ${DECRYPT_PORT}, M3U8: ${M3U8_PORT}）"
  else
    error "容器未正常运行，请查看日志。"
    docker logs "${WRAPPER_CONTAINER}" || true
    return 1
  fi
}

stop_services() {
  ensure_docker || return 1

  local stopped=false

  if docker ps -a --format '{{.Names}}' | grep -q "^${WRAPPER_CONTAINER}$"; then
    if wrapper_running; then
      info "停止 Wrapper 容器..."
      docker stop "${WRAPPER_CONTAINER}" >/dev/null && success "Wrapper 容器已停止"
      stopped=true
    fi
    docker rm "${WRAPPER_CONTAINER}" >/dev/null 2>&1 || true
  fi

  if docker ps --format '{{.Names}}' | grep -q "^${DOWNLOADER_IMAGE}$"; then
    info "停止 Downloader 容器..."
    docker stop "${DOWNLOADER_IMAGE}" >/dev/null 2>&1 || true
    docker rm "${DOWNLOADER_IMAGE}" >/dev/null 2>&1 || true
    stopped=true
  fi

  if [[ "${stopped}" == "true" ]]; then
    success "所有服务已停止"
  else
    warn "没有正在运行的服务"
  fi
}

prepare_downloader() {
  ensure_paths
  check_config

  local missing_images=()
  if [[ -z "$(docker images -q "${WRAPPER_IMAGE}")" ]]; then
    missing_images+=("${WRAPPER_IMAGE}")
  fi
  if [[ -z "$(docker images -q "${DOWNLOADER_IMAGE}")" ]]; then
    missing_images+=("${DOWNLOADER_IMAGE}")
  fi

  if [[ ${#missing_images[@]} -gt 0 ]]; then
    error "缺少镜像: ${missing_images[*]}"
    error "请先运行 ./start.sh start 构建所需镜像并启动服务"
    return 1
  fi
}

ensure_services_running() {
  if wrapper_running; then
    return 0
  fi
  warn "Wrapper 未运行，尝试启动..."
  start_services
}

run_download() {
  local url="${1:-}"; shift || true
  local extra_flags=("$@")

  if [[ -z "${url}" ]]; then
    if [[ "${NON_INTERACTIVE}" -eq 1 ]]; then
      error "无交互模式下必须提供下载链接"
      return 1
    fi
    read -rp "请输入 Apple Music 链接: " url
  fi

  if [[ -z "${url}" ]]; then
    error "未提供链接"
    return 1
  fi

  ensure_docker || return 1
  prepare_downloader || return 1
  ensure_services_running

  local downloads_mount="${DOWNLOADS_DIR}:/app/AM-DL downloads"
  local config_mount="${CONFIG_FILE}:/app/config-host.yaml:ro"

  local docker_args=(
    run --rm -i
    --user "$(id -u):$(id -g)"
    -v "${downloads_mount}"
    -v "${config_mount}"
    --network "${DOCKER_NETWORK}"
    -w /app
    -e "WRAPPER_HOST=${WRAPPER_CONTAINER}"
    "${DOWNLOADER_IMAGE}"
  )

  if [[ ${#extra_flags[@]} -gt 0 ]]; then
    docker_args+=("${extra_flags[@]}")
  fi
  docker_args+=("${url}")

  info "开始下载..."
  if [ -t 1 ]; then
    docker_args=(run --rm -it "${docker_args[@]:2}")
  fi

  if docker "${docker_args[@]}"; then
    success "下载完成，文件位于：${DOWNLOADS_DIR}"
  else
    error "下载过程中出现问题，可查看上方日志输出。"
  fi
}

download_menu() {
  local flags=()
  echo ""
  echo "选择下载类型："
  echo " 1) 单曲"
  echo " 2) 专辑 / 播放列表（默认）"
  echo " 3) 选择性下载专辑曲目"
  echo " 4) 杜比全景声"
  echo " 5) AAC 格式"
  echo " 6) 查看音质信息（debug）"
  echo " 0) 返回上一级"
  read -rp "请选择 [0-6]: " choice
  case "$choice" in
    0) return 0 ;;
    1) flags+=(--song) ;;
    3) flags+=(--select) ;;
    4) flags+=(--atmos) ;;
    5) flags+=(--aac) ;;
    6) flags+=(--debug) ;;
    *) ;;
  esac

  read -rp "请输入 Apple Music 链接（留空返回）: " url
  if [[ -z "${url}" ]]; then
    return 0
  fi
  run_download "${url}" "${flags[@]}"
}

exit_and_cleanup() {
  echo ""
  if wrapper_running; then
    info "正在停止服务..."
    docker stop "${WRAPPER_CONTAINER}" >/dev/null 2>&1 || true
    docker rm "${WRAPPER_CONTAINER}" >/dev/null 2>&1 || true
    success "服务已停止"
  fi

  read -rp "是否清理 Docker 资源? [y/N] " cleanup_choice
  if [[ "${cleanup_choice}" =~ ^[Yy]$ ]]; then
    clean_resources
  fi

  info "程序已终止..."
  exit 0
}

main_menu() {
  while true; do
    echo ""
    echo "==== Apple Music Downloader ===="
    echo " 1) 下载音乐"
    echo " 2) 查看服务状态"
    echo " 3) 查看日志"
    echo " 4) 初始化服务"
    echo " 0) 退出"
    read -rp "请选择 [0-4]: " opt
    case "$opt" in
      1) download_menu ;;
      2) show_status ;;
      3) show_logs ;;
      4) start_services ;;
      0) exit_and_cleanup ;;
      *) warn "无效选择" ;;
    esac
  done
}

main() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --non-interactive|-y)
        NON_INTERACTIVE=1
        shift
        ;;
      --use-saved)
        USE_SAVED_ONLY=1
        shift
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      *)
        break
        ;;
    esac
  done

  local action="${1:-}"; shift || true
  case "$action" in
    start)     start_services ;;
    stop)      stop_services ;;
    download)  run_download "$@" ;;
    status)    show_status ;;
    logs)      show_logs ;;
    clean)     clean_resources ;;
    help)      usage ;;
    "")        main_menu ;;
    *)         error "未知命令: $action"; usage; exit 1 ;;
  esac
}

main "$@"
