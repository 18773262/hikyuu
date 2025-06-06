/*
 *  Copyright (c) 2024 hikyuu.org
 *
 *  Created on: 2024-03-13
 *      Author: fasiondog
 */

#pragma once
#include "../MultiFactorBase.h"

namespace hku {

/**
 * @brief 指定权重合成因子 = ind1 * w1 + ind2 * w2 + ... + indn * wn
 * @param inds 原始因子列表
 * @param weights 权重列表
 * @param stks 计算证券列表
 * @param query 日期范围
 * @param ref_stk 参考证券
 * @param ic_n 默认 IC 对应的 N 日收益率
 * @param spearman 默认使用 spearman 计算相关系数，否则为 pearson
 * @param mode 获取截面数据时排序模式: 0-降序, 1-升序, 2-不排序
 * @param save_all_factors 是否保留因子数据
 * @return MultiFactorPtr
 */
MultiFactorPtr HKU_API MF_Weight(const IndicatorList& inds, const PriceList& weights,
                                 const StockList& stks, const KQuery& query, const Stock& ref_stk,
                                 int ic_n = 5, bool spearman = true, int mode = 0,
                                 bool save_all_factors = false);

MultiFactorPtr HKU_API MF_Weight();

}  // namespace hku