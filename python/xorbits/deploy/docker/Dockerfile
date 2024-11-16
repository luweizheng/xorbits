ARG BASE_CONTAINER=continuumio/miniconda3:24.9.2-0
ARG PYTHON_VERSION=3.9
FROM ${BASE_CONTAINER} AS base

FROM base AS py3.9-base
SHELL ["/bin/bash", "-c"]
RUN if [ "$PYTHON_VERSION" == "3.9" ] ; then \
    /opt/conda/bin/conda install -y python=3.9 ; fi

FROM base AS py3.10-base
SHELL ["/bin/bash", "-c"]
RUN if [ "$PYTHON_VERSION" == "3.10" ] ; then \
    /opt/conda/bin/conda install -y python=3.10 ; fi

FROM base AS py3.11-base
SHELL ["/bin/bash", "-c"]
RUN if [ "$PYTHON_VERSION" == "3.11" ] ; then \
    /opt/conda/bin/conda install -y python=3.11 ; fi

FROM base AS py3.12-base
SHELL ["/bin/bash", "-c"]
RUN if [ "$PYTHON_VERSION" == "3.12" ] ; then \
    /opt/conda/bin/conda install -y python=3.12 ; fi


FROM py${PYTHON_VERSION}-base AS deps
# TODO: UCXX is not mature enough for production, add it back when it's ready
RUN /opt/conda/bin/conda install -y anaconda::nodejs=20.17 \
  conda-forge::cython conda-forge::libnuma conda-forge::mkl \
  && pip install -U pip \
  && pip install -U \
    numpy \
    scipy \
    pandas \
    numexpr \
    psutil \
    scikit-learn \
    sqlalchemy \
    tornado \
    xoscar \
    pyarrow \
    cloudpickle \
    azure-storage-blob \
    adlfs \
    fsspec \
    s3fs \
    pyopenssl \
    datasets \
    python-kubernetes \
    jax \
    uvloop \
  && /opt/conda/bin/conda clean --all -f -y

RUN apt-get -y update \
  && apt install -y curl procps gcc g++

# Final stage
FROM deps AS final

# Copy Xorbits files
COPY . /opt/xorbits/

# Build extensions
SHELL ["/bin/bash", "-c"]
ARG PYTHON_VERSION
RUN cd /opt/xorbits/python && \
    if [ "$PYTHON_VERSION" == "3.11" ] ; \
    then CFLAGS="-DCYTHON_FAST_THREAD_STATE=0" python setup.py build_ext -i ; \
    else python setup.py build_ext -i ; fi && \
    npm cache clean --force && \
    python setup.py build_web && \
    rm -rf /opt/xorbits/python/xorbits/web/ui/node_modules

# Setup service directory
RUN mkdir -p /srv
WORKDIR /srv

# Copy and setup entrypoint scripts
RUN cp /opt/xorbits/python/xorbits/deploy/docker/entrypoint.sh /srv/entrypoint.sh && \
    cp /opt/xorbits/python/xorbits/deploy/docker/install.sh /srv/install.sh && \
    chmod a+x /srv/*.sh

# Set Python path
ENV PYTHONPATH "${PYTHONPATH}:/opt/xorbits:/opt/xorbits/python"

# Set entrypoint
ENTRYPOINT [ "/srv/entrypoint.sh" ]
