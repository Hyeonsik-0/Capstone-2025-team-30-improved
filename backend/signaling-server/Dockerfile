# Build stage (TypeScript compilation)
FROM node:20.19.5-alpine3.22 AS builder

WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# Production stage (runtime)
FROM node:20.19.5-alpine3.22 AS production

WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev
COPY --from=builder /app ./

EXPOSE 80
CMD ["npm", "start"]