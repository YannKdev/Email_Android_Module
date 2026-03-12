"use strict";
(() => {
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __commonJS = (cb, mod) => function __require() {
    return mod || (0, cb[__getOwnPropNames(cb)[0]])((mod = { exports: {} }).exports, mod), mod.exports;
  };

  // src/index.ts
  var require_index = __commonJS({
    "src/index.ts"() {
      function log(msg) {
        console.log(msg);
      }
      function safeUse(name) {
        try {
          return Java.use(name);
        } catch {
          return null;
        }
      }
      function headersToObject(headers) {
        const obj = {};
        if (!headers) return obj;
        try {
          for (let i = 0; i < headers.size(); i++) {
            obj[headers.name(i)] = headers.value(i);
          }
        } catch {
        }
        return obj;
      }
      Java.perform(() => {
        log("[\u2713] Universal HTTP hook loaded");
        try {
          const Interceptor = safeUse("okhttp3.Interceptor");
          const Builder = safeUse("okhttp3.OkHttpClient$Builder");
          const Buffer2 = safeUse("okio.Buffer");
          if (Interceptor && Builder) {
            const FridaInterceptor = Java.registerClass({
              name: "frida.OkHttp3Interceptor",
              implements: [Interceptor],
              methods: {
                intercept(chain) {
                  const request = chain.request();
                  let reqBody = null;
                  try {
                    if (request.body() && Buffer2) {
                      const buf = Buffer2.$new();
                      request.body().writeTo(buf);
                      reqBody = buf.readUtf8();
                    }
                  } catch {
                    reqBody = "[binary]";
                  }
                  const response = chain.proceed(request);
                  let respBody = null;
                  try {
                    respBody = response.peekBody(1024 * 1024).string();
                  } catch {
                    respBody = "[binary]";
                  }
                  send({
                    type: "http",
                    engine: "okhttp3",
                    url: request.url().toString(),
                    method: request.method(),
                    request: {
                      headers: headersToObject(request.headers()),
                      body: reqBody
                    },
                    response: {
                      status: response.code(),
                      headers: headersToObject(response.headers()),
                      body: respBody
                    }
                  });
                  return response;
                }
              }
            });
            Builder.build.implementation = function () {
              try {
                this.interceptors().add(FridaInterceptor.$new());
                log("[+] okhttp3 interceptor injected");
              } catch {
              }
              return this.build();
            };
          }
        } catch {
          log("[-] okhttp3 not available");
        }
        try {
          const HttpEngine = safeUse("com.android.okhttp.internal.http.HttpEngine");
          if (HttpEngine) {
            HttpEngine.readResponse.implementation = function () {
              const response = this.readResponse();
              try {
                const req = response.request();
                send({
                  type: "http",
                  engine: "android_okhttp",
                  url: req.url().toString(),
                  method: req.method(),
                  request: {
                    headers: headersToObject(req.headers()),
                    body: null
                  },
                  response: {
                    status: response.code(),
                    headers: headersToObject(response.headers()),
                    body: "[not readable]"
                  }
                });
              } catch {
              }
              return response;
            };
            log("[+] com.android.okhttp hook installed");
          }
        } catch {
          log("[-] com.android.okhttp not available");
        }
        try {
          const UrlRequestBuilder = safeUse("org.chromium.net.UrlRequest$Builder");
          if (UrlRequestBuilder) {
            UrlRequestBuilder.setHttpMethod.implementation = function (method) {
              this.__frida_method = method;
              return this.setHttpMethod(method);
            };
            UrlRequestBuilder.addHeader.implementation = function (name, value) {
              if (!this.__frida_headers) this.__frida_headers = {};
              this.__frida_headers[name] = value;
              return this.addHeader(name, value);
            };
            UrlRequestBuilder.setUploadDataProvider.implementation = function (provider, executor) {
              this.__frida_has_body = true;
              return this.setUploadDataProvider(provider, executor);
            };
            UrlRequestBuilder.build.implementation = function () {
              try {
                send({
                  type: "http",
                  engine: "cronet",
                  url: this.mUrl?.value || "[unknown]",
                  method: this.__frida_method || "GET",
                  request: {
                    headers: this.__frida_headers || {},
                    body: this.__frida_has_body ? "[streamed body]" : null
                  },
                  response: {
                    status: null,
                    headers: {},
                    body: null
                  }
                });
              } catch {
              }
              return this.build();
            };
            console.log("[+] Cronet Builder hooked (GET / POST / PUT)");
          }
        } catch {
          console.log("[-] Cronet not available");
        }
        log("[\u2713] HTTP hooks initialized");
      });
    }
  });
  require_index();
})();
