package com.dreamweaver.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.dreamweaver.common.CommonResult;
import com.dreamweaver.entity.ApiQuota;
import com.dreamweaver.mapper.ApiQuotaMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 配额管理 Controller。
 */
@Slf4j
@RestController
@RequestMapping("/internal/quota")
@RequiredArgsConstructor
public class QuotaController {

    private final ApiQuotaMapper apiQuotaMapper;

    /**
     * 查询指定用户的配额汇总。
     */
    @GetMapping("/{userId}")
    public CommonResult<List<ApiQuota>> getQuota(@PathVariable Long userId) {
        List<ApiQuota> quotas = apiQuotaMapper.selectList(
            new LambdaQueryWrapper<ApiQuota>()
                .eq(ApiQuota::getUserId, userId)
        );
        return CommonResult.ok(quotas);
    }

    /**
     * 重置指定用户的配额（管理员接口）。
     */
    @PostMapping("/reset")
    public CommonResult<String> resetQuota(@RequestParam Long userId) {
        apiQuotaMapper.delete(
            new LambdaQueryWrapper<ApiQuota>()
                .eq(ApiQuota::getUserId, userId)
        );
        log.info("配额重置: userId={}", userId);
        return CommonResult.ok("reset ok");
    }
}
