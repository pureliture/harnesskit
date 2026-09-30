# render-server

harnesskit 공유 HTML 렌더 서버. RICH 모드(optimal-response)와
grill-to-spec 이 시각화 HTML 을 브라우저로 보여줄 때 쓴다. STOP 과는 무관하다.

## 사용법

    scripts/render-server/start-server.sh --project-dir <repo-root>

랜덤 포트로 서버를 띄우고 JSON 한 줄을 출력한다:

    {"type":"server-started","url":"http://localhost:54321",
     "content_dir":".../.claude/render-server-out/content","state_dir":"..."}

## 경로 규약 (중요)

HTML 파일은 **반드시 반환 JSON 의 `content_dir` 값에 정확히** 작성한다.
서버는 `content_dir` 안의 최신 `.html`(mtime 기준)을 `/` 경로에서 서빙한다.
`content_dir` 밖(예: `render-server-out/` 직하)에 쓰면 서버가 찾지 못하고
"No HTML files found" 디버그 페이지가 나온다.

- 접속 URL: `http://localhost:<port>/` — 파일명을 URL 에 붙이지 않는다.
- 서버가 이미 떠 있으면 `start-server.sh` 가 기존 JSON 을 그대로 반환한다.

## 종료

    scripts/render-server/stop-server.sh <repo-root>/.claude/render-server-out

서버는 30분 idle 또는 owner 프로세스 종료 시 자동 종료된다.

## 동작 특성

- 브라우저 자동 갱신 없음. 새 HTML 생성 후 사용자가 수동 새로고침(F5).
- zero npm dependency (Node 내장 http/fs/path 만 사용).
