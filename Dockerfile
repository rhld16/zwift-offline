FROM ubuntu:24.04 AS builder

RUN apt update
RUN apt install --no-install-recommends -y python3.12 python3.12-venv

RUN python3.12 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
RUN pip3 install --no-cache-dir garth

FROM ubuntu:24.04
LABEL maintainer="zoffline <zoffline@tutanota.com>"

RUN apt update
RUN apt install --no-install-recommends -y python3.12 python3.12-venv
RUN apt clean
RUN rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

RUN mkdir /opt/zwift-offline
WORKDIR /opt/zwift-offline
COPY . .
RUN chmod 777 storage

EXPOSE 443 80 3024/udp 3025 53/udp

VOLUME /opt/zwift-offline/storage

ENV PYTHONUNBUFFERED=1
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

CMD [ "python", "standalone.py" ]
