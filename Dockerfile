FROM python:3.12-alpine AS builder

WORKDIR /usr/src/app

RUN apk add --no-cache git gcc g++ musl-dev libffi-dev openssl-dev file make

RUN git clone --depth 1 https://github.com/oldnapalm/zwift-offline

COPY requirements.txt requirements.txt
RUN pip install --user --requirement requirements.txt
RUN pip install --user garth

FROM python:3.12-alpine
LABEL maintainer="oldnapalm"

WORKDIR /usr/src/app

COPY --from=builder /root/.local/ /root/.local/
ENV PATH=/root/.local/bin:$PATH

COPY --from=builder /usr/src/app/zwift-offline/ zwift-offline/
RUN chmod 777 zwift-offline/storage

EXPOSE 443 80 3024/udp 3025 53/udp

VOLUME /usr/src/app/zwift-offline/storage

CMD [ "python", "zwift-offline/standalone.py" ]
