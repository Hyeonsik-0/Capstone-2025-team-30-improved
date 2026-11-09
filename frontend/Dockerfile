# Frontend Dockerfile - React 앱만 서빙
FROM node:20.19.5-bookworm-slim

WORKDIR /app

# package.json과 package-lock.json 복사
COPY package*.json ./

# 의존성 설치
RUN npm ci

# 소스 코드 복사
COPY . .

# 프로덕션 빌드 (로그 레벨을 error로 설정)
ENV VITE_LOG_LEVEL=error
RUN npm run build:prod

# serve를 사용한 정적 파일 서빙
RUN npm install -g serve@14.2.5

EXPOSE 3000

# React 앱 서빙 (SPA 라우팅 지원)
CMD ["serve", "-s", "dist", "-l", "3000"]