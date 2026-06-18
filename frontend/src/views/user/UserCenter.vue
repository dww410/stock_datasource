<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { tokenApi, type TokenBalance, type UsageStatsResponse, type UsageHistoryResponse } from '@/api/token'
import { mcpUsageApi, type McpUsageStatsResponse, type McpUsageHistoryResponse } from '@/api/mcpUsage'
import { request } from '@/utils/request'
import { MessagePlugin } from 'tdesign-vue-next'
import TokenOverviewCard from './components/TokenOverviewCard.vue'
import ConsumptionTrendChart from './components/ConsumptionTrendChart.vue'
import UsageDetailTable from './components/UsageDetailTable.vue'
import LlmConfigCard from './components/LlmConfigCard.vue'
import McpOverviewCard from './components/McpOverviewCard.vue'
import McpTrendChart from './components/McpTrendChart.vue'
import McpUsageDetailTable from './components/McpUsageDetailTable.vue'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

const activeTab = computed({
  get: () => {
    if (route.path === '/user/llm-config') return 'llm-config'
    if (route.path === '/user/mcp-usage') return 'mcp-usage'
    if (route.path === '/user/api-keys') return 'api-keys'
    return 'usage'
  },
  set: (val: string) => {
    if (val === 'llm-config') router.push('/user/llm-config')
    else if (val === 'mcp-usage') router.push('/user/mcp-usage')
    else if (val === 'api-keys') router.push('/user/api-keys')
    else router.push('/user')
  }
})
const balance = ref<TokenBalance | null>(null)
const stats = ref<UsageStatsResponse | null>(null)
const history = ref<UsageHistoryResponse | null>(null)
const loading = ref(true)
const historyPage = ref(1)
const historyPageSize = ref(20)
const historyDateRange = ref<string[]>([])

// MCP usage state
const mcpStats = ref<McpUsageStatsResponse | null>(null)
const mcpHistory = ref<McpUsageHistoryResponse | null>(null)
const mcpLoading = ref(false)
const mcpPage = ref(1)
const mcpPageSize = ref(20)
const mcpDateRange = ref<string[]>([])
const mcpDataLoaded = ref(false)

// API key state
type ApiKeyInfo = {
  id: string
  key_name: string
  api_key_prefix: string
  is_active: boolean
  last_used_at: string | null
  expires_at: string | null
  created_at: string | null
}

const apiKeys = ref<ApiKeyInfo[]>([])
const apiKeyLoading = ref(false)
const apiKeyLoaded = ref(false)

const showCreateKeyDialog = ref(false)
const creatingKey = ref(false)
const newKeyName = ref('')
const newKeyExpiresDays = ref(30)

const showCreatedKeyDialog = ref(false)
const createdApiKey = ref<string>('')

const userInitial = computed(() => {
  const name = authStore.user?.username || authStore.user?.email || '?'
  return name.charAt(0).toUpperCase()
})

const roleName = computed(() => authStore.user?.is_admin ? '管理员' : '普通用户')

const formattedDate = computed(() => {
  if (!authStore.user?.created_at) return '--'
  const d = new Date(authStore.user.created_at)
  return d.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' })
})

const formatTime = (dt: string | null) => {
  if (!dt) return '--'
  const d = new Date(dt)
  return d.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

const loadData = async () => {
  loading.value = true
  const [balanceRes, statsRes, historyRes] = await Promise.all([
    tokenApi.getBalance().catch(() => null),
    tokenApi.getStats(30).catch(() => null),
    tokenApi.getHistory({ page: historyPage.value, page_size: historyPageSize.value }).catch(() => null)
  ])
  if (balanceRes) balance.value = balanceRes
  if (statsRes) stats.value = statsRes
  if (historyRes) history.value = historyRes
  loading.value = false
}

const handlePageChange = async (page: number) => {
  historyPage.value = page
  const params: any = { page, page_size: historyPageSize.value }
  if (historyDateRange.value?.length === 2) {
    params.start_date = historyDateRange.value[0]
    params.end_date = historyDateRange.value[1]
  }
  const res = await tokenApi.getHistory(params).catch(() => null)
  if (res) history.value = res
}

const handleDateFilter = async (val: string[]) => {
  historyDateRange.value = val || []
  historyPage.value = 1
  const params: any = { page: 1, page_size: historyPageSize.value }
  if (val?.length === 2) {
    params.start_date = val[0]
    params.end_date = val[1]
  }
  const res = await tokenApi.getHistory(params).catch(() => null)
  if (res) history.value = res
}

// MCP data loading
const loadMcpData = async () => {
  if (mcpDataLoaded.value) return
  mcpLoading.value = true
  const [statsRes, historyRes] = await Promise.all([
    mcpUsageApi.getStats(30).catch(() => null),
    mcpUsageApi.getHistory({ page: mcpPage.value, page_size: mcpPageSize.value }).catch(() => null)
  ])
  if (statsRes) mcpStats.value = statsRes
  if (historyRes) mcpHistory.value = historyRes
  mcpLoading.value = false
  mcpDataLoaded.value = true
}

const handleMcpPageChange = async (page: number) => {
  mcpPage.value = page
  const params: any = { page, page_size: mcpPageSize.value }
  if (mcpDateRange.value?.length === 2) {
    params.start_date = mcpDateRange.value[0]
    params.end_date = mcpDateRange.value[1]
  }
  const res = await mcpUsageApi.getHistory(params).catch(() => null)
  if (res) mcpHistory.value = res
}

const handleMcpDateFilter = async (val: string[]) => {
  mcpDateRange.value = val || []
  mcpPage.value = 1
  const params: any = { page: 1, page_size: mcpPageSize.value }
  if (val?.length === 2) {
    params.start_date = val[0]
    params.end_date = val[1]
  }
  const res = await mcpUsageApi.getHistory(params).catch(() => null)
  if (res) mcpHistory.value = res
}

const loadApiKeys = async (force = false) => {
  if (apiKeyLoaded.value && !force) return
  apiKeyLoading.value = true
  try {
    const res = await request.get<{ keys: ApiKeyInfo[]; total: number }>('/api/mcp-api-key/list')
    apiKeys.value = res?.keys || []
    apiKeyLoaded.value = true
  } catch (e) {
    apiKeys.value = []
  } finally {
    apiKeyLoading.value = false
  }
}

const openCreateKeyDialog = () => {
  newKeyName.value = ''
  newKeyExpiresDays.value = 30
  showCreateKeyDialog.value = true
}

const handleCreateKey = async () => {
  if (!newKeyName.value.trim()) {
    MessagePlugin.warning('请输入 Key 名称')
    return
  }
  creatingKey.value = true
  try {
    const res = await request.post<{
      success: boolean
      message: string
      api_key?: string
      key_id?: string
    }>('/api/mcp-api-key/create', {
      key_name: newKeyName.value.trim(),
      expires_days: newKeyExpiresDays.value,
    })
    if (res?.success) {
      createdApiKey.value = res.api_key || ''
      showCreateKeyDialog.value = false
      showCreatedKeyDialog.value = true
      await loadApiKeys(true)
    }
  } finally {
    creatingKey.value = false
  }
}

const handleRevokeKey = async (keyId: string) => {
  try {
    const res = await request.post<{ success: boolean; message: string }>('/api/mcp-api-key/revoke', { key_id: keyId })
    if (res?.success) {
      MessagePlugin.success('已撤销')
      await loadApiKeys(true)
    }
  } catch (e) {}
}

const copyCreatedKey = async () => {
  try {
    await globalThis.navigator.clipboard.writeText(createdApiKey.value)
    MessagePlugin.success('已复制')
  } catch (e) {
    MessagePlugin.error('复制失败')
  }
}

// Lazy load MCP data when tab is switched
watch(activeTab, (val) => {
  if (val === 'mcp-usage') loadMcpData()
  if (val === 'api-keys') loadApiKeys()
})

onMounted(() => {
  loadData()
  if (activeTab.value === 'mcp-usage') loadMcpData()
  if (activeTab.value === 'api-keys') loadApiKeys()
})
</script>

<template>
  <div class="user-center">
    <!-- Profile Info Bar -->
    <div class="profile-bar">
      <div class="profile-left">
        <div class="avatar">{{ userInitial }}</div>
        <div class="profile-info">
          <h3 class="username">{{ authStore.user?.username || '--' }}</h3>
          <p class="email">{{ authStore.user?.email || '--' }}</p>
        </div>
      </div>
      <div class="profile-right">
        <div class="profile-meta">
          <span class="meta-label">注册时间</span>
          <span class="meta-value">{{ formattedDate }}</span>
        </div>
        <t-tag :theme="authStore.user?.is_admin ? 'primary' : 'default'" variant="light">
          {{ roleName }}
        </t-tag>
      </div>
    </div>

    <!-- Tabs -->
    <t-tabs v-model="activeTab" class="user-tabs">
      <t-tab-panel value="usage" label="LLM 查询记录">
        <!-- Token Overview -->
        <TokenOverviewCard :balance="balance" :stats="stats" :loading="loading" />

        <!-- Consumption Trend Chart -->
        <t-card class="section-card" :bordered="false">
          <template #title>
            <div class="card-title">消耗趋势（近30天）</div>
          </template>
          <ConsumptionTrendChart :stats="stats" :loading="loading" />
        </t-card>

        <!-- Usage Detail Table -->
        <t-card class="section-card" :bordered="false">
          <template #title>
            <div class="card-title-row">
              <span class="card-title">消耗明细</span>
              <t-date-range-picker
                class="date-filter"
                placeholder="选择日期范围"
                :clearable="true"
                @change="handleDateFilter"
              />
            </div>
          </template>
          <UsageDetailTable
            :history="history"
            :loading="loading"
            :page="historyPage"
            :page-size="historyPageSize"
            @page-change="handlePageChange"
          />
        </t-card>
      </t-tab-panel>

      <t-tab-panel value="llm-config" label="大模型配置">
        <t-card class="section-card" :bordered="false">
          <LlmConfigCard />
        </t-card>
      </t-tab-panel>

      <t-tab-panel value="mcp-usage" label="MCP 调用记录">
        <!-- MCP Overview -->
        <McpOverviewCard :stats="mcpStats" :loading="mcpLoading" />

        <!-- MCP Trend Chart -->
        <t-card class="section-card" :bordered="false">
          <template #title>
            <div class="card-title">调用趋势（近30天）</div>
          </template>
          <McpTrendChart :stats="mcpStats" :loading="mcpLoading" />
        </t-card>

        <!-- MCP Usage Detail Table -->
        <t-card class="section-card" :bordered="false">
          <template #title>
            <div class="card-title-row">
              <span class="card-title">调用明细</span>
              <t-date-range-picker
                class="date-filter"
                placeholder="选择日期范围"
                :clearable="true"
                @change="handleMcpDateFilter"
              />
            </div>
          </template>
          <McpUsageDetailTable
            :history="mcpHistory"
            :loading="mcpLoading"
            :page="mcpPage"
            :page-size="mcpPageSize"
            @page-change="handleMcpPageChange"
          />
        </t-card>
      </t-tab-panel>

      <t-tab-panel value="api-keys" label="API Key 管理">
        <t-card class="section-card" :bordered="false">
          <template #title>
            <div class="card-title-row">
              <span class="card-title">API Key 管理</span>
              <t-space>
                <t-button theme="primary" @click="openCreateKeyDialog">创建 Key</t-button>
              </t-space>
            </div>
          </template>

          <t-alert
            theme="info"
            message="API Key 用于调用开放接口：Header 添加 Authorization: Bearer sk-xxx"
            style="margin-bottom: 12px"
          />

          <t-table
            :data="apiKeys"
            :loading="apiKeyLoading"
            row-key="id"
            hover
            stripe
            size="medium"
            :columns="[
              { colKey: 'key_name', title: '名称', width: 180 },
              { colKey: 'api_key_prefix', title: '前缀', width: 140 },
              { colKey: 'created_at', title: '创建时间', width: 180 },
              { colKey: 'expires_at', title: '过期时间', width: 180 },
              { colKey: 'last_used_at', title: '最后使用', width: 180 },
              { colKey: 'op', title: '操作', width: 120, fixed: 'right' }
            ]"
          >
            <template #api_key_prefix="{ row }">
              <span class="mono">{{ row.api_key_prefix }}</span>
            </template>
            <template #created_at="{ row }">
              <span>{{ formatTime(row.created_at) }}</span>
            </template>
            <template #expires_at="{ row }">
              <span>{{ formatTime(row.expires_at) }}</span>
            </template>
            <template #last_used_at="{ row }">
              <span>{{ formatTime(row.last_used_at) }}</span>
            </template>
            <template #op="{ row }">
              <t-popconfirm content="确认撤销该 Key？撤销后将无法继续使用。" @confirm="handleRevokeKey(row.id)">
                <t-button theme="danger" variant="outline" size="small">撤销</t-button>
              </t-popconfirm>
            </template>
          </t-table>
        </t-card>

        <t-dialog
          v-model:visible="showCreateKeyDialog"
          header="创建 API Key"
          width="520px"
          :footer="false"
          destroy-on-close
        >
          <t-form label-width="100px">
            <t-form-item label="Key 名称" required-mark>
              <t-input v-model="newKeyName" placeholder="例如：dev-key" />
            </t-form-item>
            <t-form-item label="有效期">
              <t-input-number v-model="newKeyExpiresDays" :min="1" :max="3650" style="width: 200px" />
              <span style="margin-left: 8px">天</span>
            </t-form-item>
            <t-form-item label=" ">
              <t-space>
                <t-button theme="primary" :loading="creatingKey" @click="handleCreateKey">创建</t-button>
                <t-button variant="outline" @click="showCreateKeyDialog = false">取消</t-button>
              </t-space>
            </t-form-item>
          </t-form>
        </t-dialog>

        <t-dialog
          v-model:visible="showCreatedKeyDialog"
          header="API Key 已创建"
          width="600px"
          :footer="false"
          destroy-on-close
        >
          <t-alert
            theme="warning"
            message="完整 Key 只会显示一次，请立即复制保存。"
            style="margin-bottom: 12px"
          />
          <t-textarea v-model="createdApiKey" readonly :autosize="{ minRows: 2, maxRows: 4 }" />
          <t-space style="margin-top: 12px">
            <t-button
              theme="primary"
              @click="copyCreatedKey"
            >
              复制
            </t-button>
            <t-button variant="outline" @click="showCreatedKeyDialog = false">关闭</t-button>
          </t-space>
        </t-dialog>
      </t-tab-panel>
    </t-tabs>
  </div>
</template>

<style scoped>
.user-center {
  padding: 0 4px;
  min-height: 100%;
}

.profile-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-radius: 8px;
  padding: 20px 24px;
  margin-bottom: 16px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.05);
}

.profile-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.avatar {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: linear-gradient(135deg, #0052d9, #618dff);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  font-weight: 600;
  flex-shrink: 0;
}

.username {
  margin: 0 0 4px 0;
  font-size: 18px;
  font-weight: 600;
  color: #262626;
}

.email {
  margin: 0;
  font-size: 14px;
  color: #86909c;
}

.profile-right {
  display: flex;
  align-items: center;
  gap: 24px;
}

.profile-meta {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}

.meta-label {
  font-size: 12px;
  color: #86909c;
}

.meta-value {
  font-size: 14px;
  color: #262626;
  font-weight: 500;
}

.user-tabs {
  background: transparent;
}

.user-tabs :deep(.t-tabs__header) {
  background: #fff;
  border-radius: 8px 8px 0 0;
  padding: 0 16px;
  margin-bottom: 0;
}

.section-card {
  margin-bottom: 16px;
}

.card-title {
  font-size: 16px;
  font-weight: 600;
  color: #262626;
}

.card-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.date-filter {
  width: 280px;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
}
</style>
