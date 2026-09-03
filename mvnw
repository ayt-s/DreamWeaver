#!/bin/sh
# DreamWeaver 本地 Maven wrapper（git-bash 兼容）
# 绕过 MSYS 路径转换：原生 java 必须用 Windows 风格路径
# 用法：./mvnw compile / ./mvnw -version / 任意 mvn 参数
JAVA_BIN="/d/Program Files/jdk-17/bin/java"
MVN_HOME="D:/apache/apache-maven-3.9.11"
CLASSWORLDS="$MVN_HOME/boot/plexus-classworlds-2.9.0.jar"

exec "$JAVA_BIN" \
  -classpath "$CLASSWORLDS" \
  "-Dclassworlds.conf=$MVN_HOME/bin/m2.conf" \
  "-Dmaven.home=$MVN_HOME" \
  "-Dmaven.multiModuleProjectDirectory=$(pwd)" \
  org.codehaus.plexus.classworlds.launcher.Launcher "$@"