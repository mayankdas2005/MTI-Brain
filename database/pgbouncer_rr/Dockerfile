FROM public.ecr.aws/amazonlinux/amazonlinux:2023

RUN yum -y update
RUN yum install -y tar net-tools curl vim unzip less libevent-devel openssl-devel python-devel libtool git patch make gcc wget --allowerasing

# Install pandoc (required to build pgbouncer man pages)
RUN ARCH="" && if [ "$(uname -m)" = "x86_64" ]; then ARCH='amd64'; else ARCH='arm64'; fi && export ${ARCH} && \
    wget https://github.com/jgm/pandoc/releases/download/3.1.6.2/pandoc-3.1.6.2-linux-${ARCH}.tar.gz && \
    tar xvzf ./pandoc-3.1.6.2-linux-${ARCH}.tar.gz --strip-components 1 -C /usr/local

RUN git clone https://github.com/pgbouncer/pgbouncer.git && \
    cd pgbouncer && \
    git checkout -b pgbouncer-1.24 tags/pgbouncer_1_24_1 && \
    cd .. && \
    git clone https://github.com/awslabs/pgbouncer-fast-switchover.git pgbouncer-rr-patch && \
    cd pgbouncer-rr-patch && \
    ./install-pgbouncer-rr-patch.sh ../pgbouncer && \
    cd ../pgbouncer && \
    git submodule init && \
    git submodule update && \
    ./autogen.sh && \
    ./configure --prefix=/usr/local --exec-prefix=/usr/bin --bindir=/usr/bin && \
    make && \
    make install

RUN useradd -ms /bin/bash pgbouncer && \
    chown pgbouncer /home/pgbouncer && \
    chown pgbouncer /

COPY --chmod=755 ./start.sh /start.sh
COPY --chmod=644 --chown=pgbouncer:pgbouncer ./routing_rules.py /home/pgbouncer/routing_rules.py

USER pgbouncer
WORKDIR /home/pgbouncer

EXPOSE 5432

ENTRYPOINT ["/start.sh"]
