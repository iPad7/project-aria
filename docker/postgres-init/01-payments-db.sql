-- payments는 별도 DB다(docs/infra.md). 운영은 별도 인스턴스, 로컬은 같은 서버에 DB만
-- 분리한다 — outbox의 "상태 변경과 outbox 로우가 한 로컬 트랜잭션"이라는 전제가
-- 성립하려면 두 테이블이 같은 DB에 있어야 하고, aria의 테이블과는 갈려 있어야 한다.
--
-- 이 스크립트는 postgres 이미지가 **데이터 디렉터리를 처음 초기화할 때만** 실행된다.
-- 이미 쓰던 볼륨에는 적용되지 않으니 그때는 README의 CREATE DATABASE 한 줄을 쓴다.
CREATE DATABASE payments OWNER aria;
