# Elasticsearch 8.19 + IK 中文分词插件
# 预装 IK 分词器，避免运行时安装后需重启的问题
FROM elasticsearch:8.19.9

# 安装 IK 分词插件
RUN elasticsearch-plugin install --batch https://get.infini.cloud/elasticsearch/analysis-ik/8.19.9
