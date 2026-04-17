(function () {
    "use strict";

    var CONNECT_TIMEOUT_MS = 5000;
    var PING_TIMEOUT_MS = 2500;
    var HEARTBEAT_INTERVAL_MS = 15000;
    var RECONNECT_BASE_MS = 1000;
    var RECONNECT_MAX_MS = 5000;
    var RPC_TIMEOUT_MS = 20000;

    function resolveUiClass() {
        if (typeof RDStudioUltimate !== "undefined") {
            return RDStudioUltimate;
        }
        if (window.auraPro && window.auraPro.constructor) {
            return window.auraPro.constructor;
        }
        return null;
    }

    function createRpcError(instance, detail) {
        var url = (instance && instance.mcpServerUrl) || "ws://127.0.0.1:8765";
        var suffix = detail ? " " + detail : "";
        return new Error("MCP server is offline at " + url + "." + suffix);
    }

    function withTimeout(promise, timeoutMs, makeError) {
        return new Promise(function (resolve, reject) {
            var finished = false;
            var timer = setTimeout(function () {
                if (finished) {
                    return;
                }
                finished = true;
                reject(makeError());
            }, timeoutMs);

            promise.then(function (value) {
                if (finished) {
                    return;
                }
                finished = true;
                clearTimeout(timer);
                resolve(value);
            }).catch(function (error) {
                if (finished) {
                    return;
                }
                finished = true;
                clearTimeout(timer);
                reject(error);
            });
        });
    }

    function rejectPending(instance, message) {
        if (!instance.rpcPending || typeof instance.rpcPending.forEach !== "function") {
            return;
        }

        instance.rpcPending.forEach(function (pending, id) {
            try {
                if (pending && typeof pending.reject === "function") {
                    pending.reject(new Error(message || "MCP server disconnected."));
                }
            } catch (_) {}
            instance.rpcPending.delete(id);
        });
    }

    function hasModernRpcFrontend(Klass) {
        if (!Klass || !Klass.prototype) {
            return false;
        }
        return typeof Klass.prototype.verifyMcpProtocol === "function" && typeof Klass.prototype.refreshRuntimeStatus === "function";
    }

    function patchFrontend(Klass) {
        if (!Klass || Klass.prototype.__airedFrontendPatched) {
            return false;
        }
        if (hasModernRpcFrontend(Klass)) {
            try {
                console.log("[AIRD frontend.js] modern RPC frontend detected; skipping legacy patch.");
            } catch (_) {}
            return false;
        }
        Klass.prototype.__airedFrontendPatched = true;

        Klass.prototype.handleRpcSocketMessage = function (rawData) {
            var payload = null;
            try {
                payload = JSON.parse(rawData);
            } catch (error) {
                this.debugLog("RPC message parse failed", error);
                return;
            }

            if (payload && payload.status === "ok" && payload.message === "pong") {
                if (typeof this.rpcPingResolver === "function") {
                    var pingResolver = this.rpcPingResolver;
                    this.rpcPingResolver = null;
                    pingResolver(payload);
                }
                this.rpcConnected = true;
                this.rpcValidated = true;
                this.rpcLastPongAt = Date.now();
                return;
            }

            if (payload && typeof payload.id !== "undefined" && this.rpcPending && this.rpcPending.has(payload.id)) {
                var pending = this.rpcPending.get(payload.id);
                this.rpcPending.delete(payload.id);
                if (payload.error) {
                    pending.reject(new Error(payload.error.message || "RPC error"));
                } else {
                    pending.resolve(payload.result);
                }
                return;
            }

            this.debugLog("RPC event", payload);
        };

        Klass.prototype.resolveRpcUrl = function () {
            var input = document.getElementById("mcpServer");
            var nextUrl = input && input.value ? input.value.trim() : this.mcpServerUrl;
            this.mcpServerUrl = this.normalizeMcpUrl(nextUrl || "ws://127.0.0.1:8765");
            if (input) {
                input.value = this.mcpServerUrl;
            }
            return this.mcpServerUrl;
        };

        Klass.prototype.stopRpcHeartbeat = function () {
            if (this.rpcHeartbeatTimer) {
                clearInterval(this.rpcHeartbeatTimer);
                this.rpcHeartbeatTimer = null;
            }
        };

        Klass.prototype.startRpcHeartbeat = function () {
            var self = this;
            this.stopRpcHeartbeat();
            this.rpcHeartbeatTimer = setInterval(function () {
                if (!self.rpcSocket || self.rpcSocket.readyState !== WebSocket.OPEN) {
                    return;
                }
                self.validateRpcConnection("heartbeat").catch(function (error) {
                    self.debugLog("RPC heartbeat failed", error && error.message ? error.message : error);
                });
            }, HEARTBEAT_INTERVAL_MS);
        };

        Klass.prototype.scheduleRpcReconnect = function () {
            var self = this;
            if (this.rpcManualClose || this.rpcReconnectTimer) {
                return;
            }

            this.rpcReconnectAttempts = (this.rpcReconnectAttempts || 0) + 1;
            var delay = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * this.rpcReconnectAttempts);
            this.debugLog("RPC reconnect scheduled", { delay: delay, url: this.mcpServerUrl });

            this.rpcReconnectTimer = setTimeout(function () {
                self.rpcReconnectTimer = null;
                self.connectRpc(true).catch(function (error) {
                    self.debugLog("RPC reconnect failed", error && error.message ? error.message : error);
                });
            }, delay);
        };

        Klass.prototype.validateRpcConnection = function (reason) {
            var self = this;

            if (!this.rpcSocket || this.rpcSocket.readyState !== WebSocket.OPEN) {
                return Promise.reject(createRpcError(this, "WebSocket is not open."));
            }

            if (this.rpcPingPromise) {
                return this.rpcPingPromise;
            }

            this.rpcPingPromise = new Promise(function (resolve, reject) {
                var timer = setTimeout(function () {
                    self.rpcPingPromise = null;
                    self.rpcPingResolver = null;
                    self.rpcValidated = false;
                    reject(createRpcError(self, "Ping timeout during " + reason + "."));
                }, PING_TIMEOUT_MS);

                self.rpcPingResolver = function (payload) {
                    clearTimeout(timer);
                    self.rpcPingPromise = null;
                    self.rpcValidated = true;
                    self.rpcConnected = true;
                    self.rpcLastPongAt = Date.now();
                    resolve(payload);
                };

                try {
                    self.rpcSocket.send(JSON.stringify({ type: "ping" }));
                } catch (error) {
                    clearTimeout(timer);
                    self.rpcPingPromise = null;
                    self.rpcPingResolver = null;
                    reject(createRpcError(self, error && error.message ? error.message : "Unable to send ping."));
                }
            });

            return this.rpcPingPromise;
        };

        Klass.prototype.connectRpc = function () {
            var self = this;

            if (typeof WebSocket !== "function") {
                return Promise.reject(new Error("WebSocket is not supported in this Unreal WebView."));
            }

            this.resolveRpcUrl();

            if (this.rpcReconnectTimer) {
                clearTimeout(this.rpcReconnectTimer);
                this.rpcReconnectTimer = null;
            }

            if (this.rpcSocket && this.rpcSocket.readyState === WebSocket.OPEN) {
                return Promise.resolve(this.rpcSocket);
            }

            if (this.rpcSocket && this.rpcSocket.readyState === WebSocket.CONNECTING && this.rpcOpenPromise) {
                return withTimeout(this.rpcOpenPromise, CONNECT_TIMEOUT_MS, function () {
                    return createRpcError(self, "Connection timeout.");
                });
            }

            this.rpcManualClose = false;
            this.rpcOpenPromise = new Promise(function (resolve, reject) {
                var settled = false;
                var socket = null;

                try {
                    socket = new WebSocket(self.mcpServerUrl);
                    self.rpcSocket = socket;
                } catch (error) {
                    reject(createRpcError(self, error && error.message ? error.message : "Unable to create WebSocket."));
                    return;
                }

                socket.onopen = function () {
                    if (self.rpcSocket !== socket) {
                        return;
                    }
                    self.rpcConnected = true;
                    self.rpcValidated = false;
                    self.rpcReconnectAttempts = 0;
                    self.debugLog("RPC socket open", self.mcpServerUrl);
                    self.startRpcHeartbeat();
                    if (!settled) {
                        settled = true;
                        resolve(socket);
                    }
                    self.validateRpcConnection("open").then(function () {
                        if (typeof self.refreshSceneContext === "function") {
                            self.refreshSceneContext();
                        }
                    }).catch(function (error) {
                        self.debugLog("RPC validation after open failed", error && error.message ? error.message : error);
                    });
                };

                socket.onmessage = function (event) {
                    self.handleRpcSocketMessage(event.data);
                };

                socket.onerror = function (event) {
                    self.debugLog("RPC socket error", event && event.message ? event.message : event);
                    if (!settled) {
                        settled = true;
                        reject(createRpcError(self, "WebSocket open failed."));
                    }
                };

                socket.onclose = function (event) {
                    if (self.rpcSocket === socket) {
                        self.rpcConnected = false;
                        self.rpcValidated = false;
                        self.stopRpcHeartbeat();
                        rejectPending(self, "MCP server disconnected (" + (event && event.code ? event.code : 1006) + ").");
                        if (!self.rpcManualClose) {
                            self.scheduleRpcReconnect();
                        }
                    }

                    if (!settled) {
                        settled = true;
                        reject(createRpcError(self, "Socket closed during connect."));
                    }
                };
            });

            return withTimeout(this.rpcOpenPromise, CONNECT_TIMEOUT_MS, function () {
                return createRpcError(self, "Connection timeout.");
            });
        };

        Klass.prototype.waitForRpcConnection = async function () {
            await this.connectRpc();
            if (!this.rpcValidated || !this.rpcLastPongAt || (Date.now() - this.rpcLastPongAt) > (HEARTBEAT_INTERVAL_MS * 2)) {
                await this.validateRpcConnection("wait");
            }
            if (!this.rpcSocket || this.rpcSocket.readyState !== WebSocket.OPEN) {
                throw createRpcError(this, "WebSocket is not open.");
            }
            return this.rpcSocket;
        };

        Klass.prototype.rpcCall = async function (method, params) {
            var self = this;
            await this.waitForRpcConnection();

            return new Promise(function (resolve, reject) {
                if (!self.rpcSocket || self.rpcSocket.readyState !== WebSocket.OPEN) {
                    reject(createRpcError(self, "WebSocket is not open."));
                    return;
                }

                var id = self.rpcId++;
                var timer = setTimeout(function () {
                    self.rpcPending.delete(id);
                    reject(createRpcError(self, "RPC timeout for " + method + "."));
                }, RPC_TIMEOUT_MS);

                self.rpcPending.set(id, {
                    resolve: function (value) {
                        clearTimeout(timer);
                        resolve(value);
                    },
                    reject: function (error) {
                        clearTimeout(timer);
                        reject(error instanceof Error ? error : new Error(String(error)));
                    }
                });

                try {
                    self.rpcSocket.send(JSON.stringify({
                        jsonrpc: "2.0",
                        id: id,
                        method: method,
                        params: params || {}
                    }));
                } catch (error) {
                    clearTimeout(timer);
                    self.rpcPending.delete(id);
                    reject(createRpcError(self, error && error.message ? error.message : "Unable to send RPC message."));
                }
            });
        };

        Klass.prototype.initRpcConnection = function () {
            if (!this.rpcPending || typeof this.rpcPending.set !== "function") {
                this.rpcPending = new Map();
            }
            this.rpcReconnectAttempts = 0;
            this.rpcValidated = false;
            this.rpcLastPongAt = 0;
            this.rpcManualClose = false;
            this.resolveRpcUrl();
            this.connectRpc().catch(function (error) {
                this.debugLog("Initial RPC connect failed", error && error.message ? error.message : error);
            }.bind(this));
        };

        Klass.prototype.enableMCP = function () {
            var self = this;
            var raw = document.getElementById("mcpServer") ? document.getElementById("mcpServer").value : "ws://127.0.0.1:8765";
            var server = this.normalizeMcpUrl(raw || "ws://127.0.0.1:8765");
            localStorage.setItem("mcp_server", server);
            this.mcpServerUrl = server;

            if (this.rpcSocket) {
                try {
                    this.rpcManualClose = false;
                    this.rpcSocket.close();
                } catch (_) {}
            }

            this.rpcSocket = null;
            this.rpcValidated = false;
            this.connectRpc().then(function () {
                return self.validateRpcConnection("enable");
            }).then(function () {
                self.addMessage("MCP enabled: " + server, "assistant");
            }).catch(function (error) {
                self.addMessage("MCP server offline: " + (error && error.message ? error.message : error), "assistant");
            });
        };
        return true;
    }

    var Klass = resolveUiClass();
    if (!Klass) {
        console.error("AIRD frontend patch failed: RDStudioUltimate is unavailable.");
        return;
    }

    var legacyPatched = patchFrontend(Klass);

    if (legacyPatched && window.auraPro) {
        if (window.auraPro.rpcSocket) {
            try {
                window.auraPro.rpcManualClose = true;
                window.auraPro.rpcSocket.close();
            } catch (_) {}
            window.auraPro.rpcSocket = null;
            window.auraPro.rpcManualClose = false;
        }
        window.auraPro.initRpcConnection();
    }
})();
