export type DatasetProfileStatus = 'ready' | 'unavailable';

export interface DatasetProfile {
  profile_id: string;
  display_name: string;
  capabilities: string[];
  status: DatasetProfileStatus;
}

export const DEFAULT_DATASET_PROFILES: DatasetProfile[] = [
  {
    profile_id: 'chinook-music',
    display_name: 'Chinook + 非结构化知识库',
    capabilities: ['sql', 'rag', 'calculate'],
    status: 'ready',
  },
  {
    profile_id: 'adventureworks',
    display_name: 'AdventureWorks PostgreSQL',
    capabilities: ['sql'],
    status: 'unavailable',
  },
];

export const PROFILE_EXAMPLES: Record<string, { examples: string[]; clarification: string }> = {
  'chinook-music': {
    examples: ['客户数量是多少？', '美国客户数量是多少？', '有哪些音乐类型？', '2025年第三季度音频销售额是多少？', '销售额口径是什么？'],
    clarification: '第三季度 Rock 达标了吗？',
  },
  adventureworks: {
    examples: ['订单数是多少？', '客户数量是多少？', '销量最高的产品是什么？', '2013年销售额是多少？', '按产品查看销售额排名。'],
    clarification: '2013年销售额是多少？',
  },
};
