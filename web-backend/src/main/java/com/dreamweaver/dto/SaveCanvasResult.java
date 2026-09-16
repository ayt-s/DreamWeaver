package com.dreamweaver.dto;

import lombok.Data;

/**
 * 保存画布的结果。
 *
 * <p>{@code conflict=true} 表示乐观锁版本不符（画布已在别处被修改），本次**未写入**，
 * 并带上服务端现状，供前端决定「用我的覆盖」还是「放弃我的改动」。</p>
 */
@Data
public class SaveCanvasResult {

    /** 保存成功后的画布；conflict=true 时为 null */
    private CanvasProjectView canvas;

    /** true = 版本冲突，本次未写入 */
    private boolean conflict;

    /** 服务端当前版本与内容（仅 conflict=true 时有值） */
    private Integer serverVersion;
    private String serverNodesJson;
    private String serverEdgesJson;
}
