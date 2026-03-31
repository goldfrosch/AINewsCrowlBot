#!/bin/bash

COMPOSE_CMD="sudo docker compose"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

print_header() {
  echo ""
  echo -e "${BOLD}${CYAN}==============================${RESET}"
  echo -e "${BOLD}${CYAN}   Docker Compose Manager${RESET}"
  echo -e "${BOLD}${CYAN}==============================${RESET}"
  echo ""
}

print_status() {
  echo -e "${GREEN}[✔]${RESET} $1"
}

print_error() {
  echo -e "${RED}[✘]${RESET} $1"
}

print_warn() {
  echo -e "${YELLOW}[!]${RESET} $1"
}

check_compose_file() {
  if [ ! -f "docker-compose.yml" ] && [ ! -f "docker-compose.yaml" ]; then
    print_error "현재 디렉토리에 docker-compose.yml 파일이 없어."
    echo -e "    현재 위치: ${YELLOW}$(pwd)${RESET}"
    echo ""
    exit 1
  fi
}

do_build() {
  echo ""
  print_status "이미지 빌드 시작..."
  $COMPOSE_CMD build
  if [ $? -eq 0 ]; then
    print_status "빌드 완료."
  else
    print_error "빌드 실패."
  fi
}

do_up() {
  echo ""
  print_status "컨테이너 백그라운드 실행 시작..."
  $COMPOSE_CMD up -d
  if [ $? -eq 0 ]; then
    print_status "실행 완료."
    echo ""
    $COMPOSE_CMD ps
  else
    print_error "실행 실패."
  fi
}

do_down() {
  echo ""
  print_warn "컨테이너를 중지하고 삭제할게. 계속할까? (y/N)"
  read -r confirm
  if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
    $COMPOSE_CMD down
    if [ $? -eq 0 ]; then
      print_status "중지 및 삭제 완료."
    else
      print_error "중지/삭제 실패."
    fi
  else
    print_warn "취소했어."
  fi
}

do_restart() {
  echo ""
  print_status "컨테이너 재시작 중..."
  $COMPOSE_CMD restart
  if [ $? -eq 0 ]; then
    print_status "재시작 완료."
    echo ""
    $COMPOSE_CMD ps
  else
    print_error "재시작 실패."
  fi
}

do_logs() {
  echo ""
  print_status "로그 출력 중... (종료: Ctrl+C)"
  echo ""
  $COMPOSE_CMD logs -f
}

do_ps() {
  echo ""
  print_status "현재 컨테이너 상태:"
  echo ""
  $COMPOSE_CMD ps
}

check_compose_file

while true; do
  print_header
  echo -e "  ${BOLD}1)${RESET} 빌드          (build)"
  echo -e "  ${BOLD}2)${RESET} 실행          (up -d)"
  echo -e "  ${BOLD}3)${RESET} 중지/삭제     (down)"
  echo -e "  ${BOLD}4)${RESET} 재시작        (restart)"
  echo -e "  ${BOLD}5)${RESET} 로그 보기     (logs -f)"
  echo -e "  ${BOLD}6)${RESET} 상태 확인     (ps)"
  echo -e "  ${BOLD}0)${RESET} 종료"
  echo ""
  echo -ne "${BOLD}선택 >> ${RESET}"
  read -r choice

  case $choice in
    1) do_build ;;
    2) do_up ;;
    3) do_down ;;
    4) do_restart ;;
    5) do_logs ;;
    6) do_ps ;;
    0)
      echo ""
      print_status "종료할게."
      echo ""
      exit 0
      ;;
    *)
      echo ""
      print_error "잘못된 입력이야. 0~6 중에서 골라."
      ;;
  esac

  echo ""
  echo -e "${YELLOW}엔터를 누르면 메뉴로 돌아가.${RESET}"
  read -r _pause
done
