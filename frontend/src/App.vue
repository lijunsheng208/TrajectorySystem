<script setup>
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import L from 'leaflet'
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Circle,
  LoaderCircle,
  MapPinned,
  Search,
  Upload,
  X,
} from 'lucide-vue-next'

const PAGE_SIZE = 20
const ROLES = [
  {
    key: 'query', code: 'Q', label: '查询轨迹', fileName: 'query_traj_od.csv',
    uploadPath: 'files/query', listPath: 'trajectories/query',
  },
  {
    key: 'query-sim', code: 'S', label: '相似正例', fileName: 'query_sim_traj_od.csv',
    uploadPath: 'files/query-sim', listPath: 'trajectories/query-sim',
  },
  {
    key: 'database', code: 'D', label: '轨迹数据库', fileName: 'database_traj_od.csv',
    uploadPath: 'files/database', listPath: 'trajectories/database',
  },
]

const evaluationId = ref('')
const fileStates = ref(Object.fromEntries(ROLES.map(({ key }) => [key, {
  status: 'idle', fileName: '', error: '', result: null,
}])))
const activeRole = ref('')
const tracks = ref([])
const selectedTrack = ref(null)
const selectedTrackId = ref('')
const query = ref('')
const activeQuery = ref('')
const page = ref(1)
const pageJump = ref('1')
const pageCount = ref(1)
const totalTracks = ref(0)
const isLoadingList = ref(false)
const isLoadingDetail = ref(false)
const listError = ref('')
const detailError = ref('')
const errorDialog = ref(null)
const errorDialogTitle = ref('')
const errorDialogMessage = ref('')

let evaluationPromise = null
let map = null
let routeLayer = null
let endpointLayer = null
let listRequestSequence = 0
let detailRequestSequence = 0

const activeRoleConfig = computed(() => ROLES.find(({ key }) => key === activeRole.value))
const hasUploadedFile = computed(() => ROLES.some(({ key }) => fileStates.value[key].status === 'success'))
const allFilesReady = computed(() => ROLES.every(({ key }) => fileStates.value[key].status === 'success'))
const selectionSummary = computed(() => {
  if (!selectedTrack.value) return null
  const { gps, time } = selectedTrack.value
  return {
    points: gps.length,
    start: formatTime(time[0]),
    end: formatTime(time[time.length - 1]),
    duration: formatDuration((time[time.length - 1] ?? 0) - (time[0] ?? 0)),
  }
})

async function fetchJson(url, options) {
  const response = await fetch(url, options)
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(payload?.error?.message || `请求失败（HTTP ${response.status}）`)
  }
  return payload
}

async function ensureEvaluation() {
  if (evaluationId.value) return evaluationId.value
  if (!evaluationPromise) {
    evaluationPromise = fetchJson('/api/evaluations', { method: 'POST' })
      .then((payload) => {
        evaluationId.value = payload.evaluation_id
        return payload.evaluation_id
      })
      .finally(() => { evaluationPromise = null })
  }
  return evaluationPromise
}

async function processRoleFile(role, file) {
  if (!file) return
  const config = ROLES.find(({ key }) => key === role)
  const state = fileStates.value[role]
  state.fileName = file.name
  state.error = ''

  state.status = 'uploading'
  try {
    const currentEvaluationId = await ensureEvaluation()
    const formData = new FormData()
    formData.append('file', file)
    const payload = await fetchJson(
      `/api/evaluations/${currentEvaluationId}/${config.uploadPath}`,
      { method: 'PUT', body: formData },
    )
    state.status = 'success'
    state.result = payload.files[role]
    activeRole.value = role
    resetViewer()
    await loadTrajectoryPage(1, true)
  } catch (error) {
    state.status = state.result ? 'success' : 'error'
    state.error = error.message || '文件上传失败'
  }
}

function onFileChange(role, event) {
  processRoleFile(role, event.target.files?.[0])
  event.target.value = ''
}

function chooseFile(role) {
  document.getElementById(`file-${role}`)?.click()
}

function showUploadError(role) {
  const config = ROLES.find(({ key }) => key === role)
  const message = fileStates.value[role].error
  if (!message) return
  errorDialogTitle.value = `${config.label}上传失败`
  errorDialogMessage.value = message
  errorDialog.value?.showModal()
}

function closeErrorDialog() {
  errorDialog.value?.close()
}

function normalizeSummary(item) {
  return {
    id: item.trajectory_id,
    userId: item.user_id,
    callType: item.call_type,
    roadLength: item.road_len,
    gpsLength: item.gps_len,
  }
}

async function switchRole(role) {
  if (fileStates.value[role].status !== 'success' || role === activeRole.value) return
  activeRole.value = role
  resetViewer()
  await loadTrajectoryPage(1, true)
}

function resetViewer() {
  tracks.value = []
  selectedTrack.value = null
  selectedTrackId.value = ''
  query.value = ''
  activeQuery.value = ''
  page.value = 1
  pageJump.value = '1'
  pageCount.value = 1
  totalTracks.value = 0
  listError.value = ''
  detailError.value = ''
  isLoadingList.value = false
  isLoadingDetail.value = false
  listRequestSequence += 1
  detailRequestSequence += 1
  clearMapLayers()
}

async function loadTrajectoryPage(targetPage, selectFirst = false) {
  if (!evaluationId.value || !activeRole.value) return
  const requestSequence = ++listRequestSequence
  isLoadingList.value = true
  listError.value = ''
  try {
    const parameters = new URLSearchParams({ page: String(targetPage), page_size: String(PAGE_SIZE) })
    if (activeQuery.value) parameters.set('search', activeQuery.value)
    const payload = await fetchJson(
      `/api/evaluations/${evaluationId.value}/${activeRoleConfig.value.listPath}?${parameters}`,
    )
    if (requestSequence !== listRequestSequence) return
    tracks.value = payload.items.map(normalizeSummary)
    page.value = payload.page
    pageJump.value = String(payload.page)
    pageCount.value = Math.max(1, payload.total_pages)
    totalTracks.value = payload.total
    if (selectFirst && tracks.value.length) await selectTrack(tracks.value[0])
    else if (selectFirst) clearSelection()
  } catch (error) {
    if (requestSequence !== listRequestSequence) return
    tracks.value = []
    listError.value = error.message || '轨迹列表加载失败'
  } finally {
    if (requestSequence === listRequestSequence) isLoadingList.value = false
  }
}

function clearSelection() {
  detailRequestSequence += 1
  selectedTrack.value = null
  selectedTrackId.value = ''
  detailError.value = ''
  isLoadingDetail.value = false
  clearMapLayers()
}

async function selectTrack(track) {
  if (!evaluationId.value || !track?.id) return
  const requestSequence = ++detailRequestSequence
  selectedTrackId.value = track.id
  selectedTrack.value = null
  detailError.value = ''
  isLoadingDetail.value = true
  clearMapLayers()
  try {
    const payload = await fetchJson(
      `/api/evaluations/${evaluationId.value}/trajectories/${track.id}`,
    )
    if (requestSequence !== detailRequestSequence) return
    const detail = {
      ...normalizeSummary(payload),
      road: payload.road,
      gps: payload.gps,
      time: payload.time,
      ptime: payload.ptime,
    }
    selectedTrack.value = detail
    await nextTick()
    drawTrack(detail)
  } catch (error) {
    if (requestSequence !== detailRequestSequence) return
    selectedTrackId.value = ''
    detailError.value = error.message || '轨迹详情加载失败'
  } finally {
    if (requestSequence === detailRequestSequence) isLoadingDetail.value = false
  }
}

function formatTime(timestamp) {
  if (!Number.isFinite(Number(timestamp))) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: false,
  }).format(new Date(Number(timestamp) * 1000))
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return '—'
  const minutes = Math.floor(seconds / 60)
  const remain = Math.round(seconds % 60)
  return minutes ? `${minutes} 分 ${remain} 秒` : `${remain} 秒`
}

function initMap() {
  if (map) return
  map = L.map('trajectory-map', { zoomControl: false, preferCanvas: true }).setView([41.1579, -8.6291], 13)
  L.control.zoom({ position: 'bottomright' }).addTo(map)
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors',
  }).addTo(map)
}

function clearMapLayers() {
  routeLayer?.remove()
  endpointLayer?.remove()
  routeLayer = null
  endpointLayer = null
}

function drawTrack(track) {
  initMap()
  clearMapLayers()
  if (!track?.gps?.length) return
  const latLngs = track.gps.map(([longitude, latitude]) => [latitude, longitude])
  routeLayer = L.polyline(latLngs, {
    color: '#176b55', weight: 4, opacity: 0.94, lineCap: 'round', lineJoin: 'round',
  }).addTo(map)
  const start = latLngs[0]
  const end = latLngs[latLngs.length - 1]
  endpointLayer = L.layerGroup([
    L.circleMarker(start, { radius: 7, color: '#fff', weight: 2, fillColor: '#176b55', fillOpacity: 1 })
      .bindTooltip('起点', { direction: 'top', offset: [0, -6] }),
    L.circleMarker(end, { radius: 7, color: '#fff', weight: 2, fillColor: '#c46b3c', fillOpacity: 1 })
      .bindTooltip('终点', { direction: 'top', offset: [0, -6] }),
  ]).addTo(map)
  map.fitBounds(routeLayer.getBounds(), { padding: [54, 54], maxZoom: 16 })
  setTimeout(() => map?.invalidateSize(), 0)
}

function searchTrajectories() {
  activeQuery.value = query.value.trim()
  loadTrajectoryPage(1, true)
}

function clearSearch() {
  if (!query.value && !activeQuery.value) return
  query.value = ''
  activeQuery.value = ''
  loadTrajectoryPage(1, true)
}

function jumpToPage() {
  const requestedPage = Number(pageJump.value)
  if (!Number.isInteger(requestedPage)) {
    pageJump.value = String(page.value)
    return
  }
  const targetPage = Math.min(pageCount.value, Math.max(1, requestedPage))
  pageJump.value = String(targetPage)
  if (targetPage !== page.value && !isLoadingList.value) loadTrajectoryPage(targetPage, true)
}

onBeforeUnmount(() => {
  listRequestSequence += 1
  detailRequestSequence += 1
  map?.remove()
  map = null
})
</script>

<template>
  <main class="app-shell">
    <header class="topbar">
      <div class="brand-lockup">
        <div class="brand-mark" aria-hidden="true"><MapPinned :size="20" /></div>
        <div>
          <h1>轨迹相似性评测系统</h1>
          <p>Trajectory Similarity Evaluation</p>
        </div>
      </div>
      <div class="evaluation-state" :class="{ ready: allFilesReady }">
        <span></span>{{ allFilesReady ? '评测数据已就绪' : '等待三个轨迹文件' }}
      </div>
    </header>

    <section class="workspace">
      <aside class="data-panel" aria-label="轨迹数据面板">
        <div class="panel-heading">
          <div><h2>轨迹文件</h2></div>
        </div>

        <div class="upload-list">
          <div v-for="role in ROLES" :key="role.key" class="upload-row" :class="fileStates[role.key].status">
            <input
              :id="`file-${role.key}`"
              type="file"
              accept=".csv,text/csv"
              @change="onFileChange(role.key, $event)"
            />
            <div class="file-code">{{ role.code }}</div>
            <div class="file-copy">
              <strong>{{ role.label }}</strong>
              <span>{{ role.fileName }}</span>
              <small v-if="fileStates[role.key].error" class="file-error">
                {{ fileStates[role.key].error }}
              </small>
              <small v-else-if="fileStates[role.key].status === 'success'">
                {{ fileStates[role.key].result.trajectory_count.toLocaleString() }} 条轨迹
              </small>
            </div>
            <LoaderCircle v-if="fileStates[role.key].status === 'uploading'" class="spin state-icon" :size="17" />
            <button
              v-else-if="fileStates[role.key].error"
              class="error-detail-button"
              type="button"
              title="查看完整错误信息"
              :aria-label="`查看${role.label}的完整错误信息`"
              @click="showUploadError(role.key)"
            >
              <AlertTriangle :size="17" />
            </button>
            <CheckCircle2 v-else-if="fileStates[role.key].status === 'success'" class="state-icon" :size="17" />
            <Circle v-else class="state-icon idle-icon" :size="15" />
            <button
              class="upload-action"
              type="button"
              :title="`选择 ${role.fileName}`"
              :disabled="fileStates[role.key].status === 'uploading'"
              @click="chooseFile(role.key)"
            >
              <Upload :size="15" />{{ fileStates[role.key].status === 'success' ? '更换' : '上传' }}
            </button>
          </div>
        </div>

        <div v-if="hasUploadedFile" class="source-tabs" role="tablist" aria-label="轨迹数据源">
          <button
            v-for="role in ROLES"
            :key="role.key"
            type="button"
            role="tab"
            :class="{ active: activeRole === role.key }"
            :disabled="fileStates[role.key].status !== 'success'"
            :aria-selected="activeRole === role.key"
            @click="switchRole(role.key)"
          >
            <b>{{ role.code }}</b>{{ role.label }}
          </button>
        </div>

        <div v-if="activeRole" class="list-toolbar">
          <form class="list-search" role="search" @submit.prevent="searchTrajectories">
            <label class="search-field">
              <input v-model="query" type="search" placeholder="完整轨迹编号或用户 ID" />
              <button v-if="query" class="field-clear" type="button" title="清除搜索" @click="clearSearch">
                <X :size="14" />
              </button>
            </label>
            <button class="icon-button" type="submit" title="搜索全部轨迹" :disabled="isLoadingList">
              <Search :size="16" />
            </button>
          </form>
          <span>{{ totalTracks.toLocaleString() }} 条</span>
        </div>

        <div class="trajectory-list" role="listbox" :aria-label="`${activeRoleConfig?.label || ''}列表`">
          <div v-if="isLoadingList" class="empty-list compact">
            <LoaderCircle class="spin" :size="20" /><strong>正在加载轨迹</strong>
          </div>
          <div v-else-if="listError" class="empty-list compact error-copy">
            <AlertTriangle :size="20" /><strong>列表加载失败</strong><p>{{ listError }}</p>
          </div>
          <template v-else>
            <button
              v-for="track in tracks"
              :key="track.id"
              class="trajectory-row"
              :class="{ selected: selectedTrackId === track.id }"
              type="button"
              role="option"
              :aria-selected="selectedTrackId === track.id"
              @click="selectTrack(track)"
            >
              <span class="track-id">{{ track.id }}</span>
              <span class="track-user">用户 {{ track.userId }}</span>
              <span class="track-points">{{ track.gpsLength }} 点</span>
            </button>
            <div v-if="activeQuery && !tracks.length" class="empty-list compact">
              <strong>没有匹配的轨迹</strong><p>{{ activeQuery }}</p>
            </div>
            <div v-else-if="!tracks.length" class="empty-list">
              <span class="axis-symbol">φ / λ</span>
              <strong>{{ hasUploadedFile ? '请选择数据源' : '暂无轨迹数据' }}</strong>
              <p>{{ hasUploadedFile ? '从已上传的文件中选择要查看的轨迹。' : '上传任意一个轨迹文件后即可预览。' }}</p>
            </div>
          </template>
        </div>

        <nav v-if="activeRole && totalTracks" class="pagination" aria-label="轨迹列表分页">
          <span><b>{{ page }}</b> / {{ pageCount }}</span>
          <form class="page-jump" @submit.prevent="jumpToPage">
            <input v-model="pageJump" type="text" inputmode="numeric" aria-label="跳转页码" />
            <button class="icon-button" type="submit" title="跳转到指定页" :disabled="isLoadingList">
              <ArrowRight :size="16" />
            </button>
          </form>
        </nav>
      </aside>

      <section class="map-panel" aria-label="轨迹地图">
        <div id="trajectory-map" class="map-canvas"></div>
        <div v-if="selectedTrack" class="map-titlebar">
          <div><p class="section-index">MAP / PORTO</p><h2>{{ selectedTrack.id }}</h2></div>
          <div class="legend"><i></i>{{ activeRoleConfig?.label }}</div>
        </div>
        <div v-if="selectionSummary" class="coordinate-strip">
          <div><span>轨迹点</span><strong>{{ selectionSummary.points }}</strong></div>
          <div><span>起始时间</span><strong>{{ selectionSummary.start }}</strong></div>
          <div><span>结束时间</span><strong>{{ selectionSummary.end }}</strong></div>
          <div><span>持续时间</span><strong>{{ selectionSummary.duration }}</strong></div>
        </div>
        <div v-if="isLoadingDetail" class="map-empty">
          <div class="map-empty-grid" aria-hidden="true"></div><LoaderCircle class="spin" :size="28" /><h2>正在加载轨迹</h2>
        </div>
        <div v-else-if="detailError" class="map-empty error-copy">
          <div class="map-empty-grid" aria-hidden="true"></div><AlertTriangle :size="28" /><h2>轨迹加载失败</h2><p>{{ detailError }}</p>
        </div>
        <div v-else-if="!selectedTrack" class="map-empty">
          <div class="map-empty-grid" aria-hidden="true"></div><MapPinned :size="28" /><h2>等待轨迹数据</h2><p>选择已上传文件中的轨迹后，地图将在此显示。</p>
        </div>
      </section>
    </section>

    <dialog ref="errorDialog" class="error-dialog" @click.self="closeErrorDialog">
      <div class="error-dialog-header">
        <div class="error-dialog-title">
          <AlertTriangle :size="19" />
          <h2>{{ errorDialogTitle }}</h2>
        </div>
        <button class="icon-button" type="button" title="关闭错误信息" @click="closeErrorDialog">
          <X :size="17" />
        </button>
      </div>
      <p>{{ errorDialogMessage }}</p>
      <button class="dialog-confirm" type="button" @click="closeErrorDialog">关闭</button>
    </dialog>
  </main>
</template>
