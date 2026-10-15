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
    status: 'ready',
  },
];
