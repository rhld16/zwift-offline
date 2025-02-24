FROM ubuntu/python:3.12-24.04
LABEL maintainer="zoffline <zoffline@tutanota.com>"

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install --no-install-recommends -y python3.12-venv && \
	apt-get clean && rm -rf /var/lib/apt/lists/*

RUN python3.12 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" VIRTUAL_ENV=/opt/venv PYTHONUNBUFFERED=1

WORKDIR /opt/zwift-offline
COPY . .

RUN pip3 install --no-cache-dir -r requirements.txt && \
	pip3 install --no-cache-dir garth && \
	chmod 777 storage

EXPOSE 443 80 3024/udp 3025 53/udp
VOLUME /opt/zwift-offline/storage

CMD [ "python", "standalone.py" ]
