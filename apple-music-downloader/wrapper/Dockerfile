FROM debian:stable-slim
WORKDIR /app
#COPY --from=builder /app /app
COPY . /app
RUN chmod +x /app/wrapper
ENV args=""
CMD ["bash", "-c", "./wrapper ${args}"]
EXPOSE 10020 20020 30020
