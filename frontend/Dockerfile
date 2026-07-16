FROM node:20-alpine AS build

WORKDIR /app/frontend

RUN npm config set registry https://registry.npmmirror.com

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend /app/frontend

ARG VITE_API_BASE_URL=/api
ARG VITE_GIT_SHA=unknown
ARG VITE_BUILD_TIME=unknown
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_GIT_SHA=${VITE_GIT_SHA}
ENV VITE_BUILD_TIME=${VITE_BUILD_TIME}

RUN npm run build

FROM nginx:1.27-alpine

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html

EXPOSE 80
