FROM ubuntu:24.04
LABEL maintainer="zoffline <zoffline@tutanota.com>"

RUN apt-get update
RUN apt-get install --no-install-recommends -y python3.12 python3.12-venv
RUN apt-get clean
RUN rm -rf /var/lib/apt/lists/*

RUN python3.12 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV VIRTUAL_ENV=/opt/venv
ENV PYTHONUNBUFFERED=1

RUN mkdir /opt/zwift-offline
WORKDIR /opt/zwift-offline
COPY . .

RUN pip3 install --no-cache-dir -r requirements.txt
RUN pip3 install --no-cache-dir garth

RUN chmod 777 storage

EXPOSE 443 80 3024/udp 3025 53/udp

VOLUME /opt/zwift-offline/storage

CMD [ "python", "standalone.py" ]
