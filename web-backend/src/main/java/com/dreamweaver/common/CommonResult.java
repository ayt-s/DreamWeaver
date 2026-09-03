package com.dreamweaver.common;

import lombok.Data;

/**
 * 统一返回体：{code, message, data}。
 */
@Data
public class CommonResult<T> {

    private int code;
    private String message;
    private T data;

    public static <T> CommonResult<T> ok(T data) {
        CommonResult<T> r = new CommonResult<>();
        r.code = 0;
        r.message = "ok";
        r.data = data;
        return r;
    }

    public static <T> CommonResult<T> error(int code, String message) {
        CommonResult<T> r = new CommonResult<>();
        r.code = code;
        r.message = message;
        return r;
    }
}