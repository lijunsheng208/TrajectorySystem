<script setup>
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import L from 'leaflet'
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  FileText,
  LoaderCircle,
  MapPinned,
  Search,
  Upload,
  X,
} from 'lucide-vue-next'

const PAGE_SIZE = 20

const fileInput = ref(null)
const fileName = ref('')
const tracks = ref([])
const selectedTrack = ref(null)
const query = ref('')
const page = ref(1)
const isParsing = ref(false)
const parseProgress = ref(0)
const errorMessage = ref('')
const isDragging = ref(false)
const uploadResult = ref(null)
const totalTracks = ref(0)
const pageCount = ref(1)
const isLoadingList = ref(false)
const isLoadingDetail = ref(false)
const listError = ref('')
const detailError = ref('')
const selectedTrackId = ref('')
const activeQuery = ref('')
const pageJump = ref('1')

let map = null
let routeLayer = null
let endpointLayer = null
let listRequestSequence = 0
let detailRequestSequence = 0

const visibleTracks = computed(() => tracks.value)

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

async function processFile(file) {
  if (!file) return
  if (file.name !== 'query_traj_od.csv') {
    errorMessage.value = '第一阶段只允许上传 query_traj_od.csv'
    return
  }

  resetData(false)
  fileName.value = file.name
  isParsing.value = true
  parseProgress.value = 30

  try {
    const formData = new FormData()
    formData.append('file', file)
    const response = await fetch('/api/uploads', { method: 'POST', body: formData })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(payload?.error?.message || `上传失败（HTTP ${response.status}）`)
    }
    uploadResult.value = payload
    parseProgress.value = 100
    await loadTrajectoryPage(1, true)
  } catch (error) {
    errorMessage.value = error.message || '文件上传失败'
  } finally {
    isParsing.value = false
  }
}

async function fetchJson(url) {
  const response = await fetch(url)
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(payload?.error?.message || `请求失败（HTTP ${response.status}）`)
  }
  return payload
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

async function loadTrajectoryPage(targetPage, selectFirst = false) {
  if (!uploadResult.value?.upload_id) return
  const requestSequence = ++listRequestSequence
  isLoadingList.value = true
  listError.value = ''

  try {
    const parameters = new URLSearchParams({
      page: String(targetPage),
      page_size: String(PAGE_SIZE),
    })
    if (activeQuery.value) parameters.set('search', activeQuery.value)
    const payload = await fetchJson(`/api/uploads/${uploadResult.value.upload_id}/trajectories?${parameters}`)
    if (requestSequence !== listRequestSequence) return
    tracks.value = payload.items.map(normalizeSummary)
    page.value = payload.page
    pageJump.value = String(payload.page)
    pageCount.value = Math.max(1, payload.total_pages)
    totalTracks.value = payload.total
    if (selectFirst && tracks.value.length) {
      await selectTrack(tracks.value[0])
    } else if (selectFirst) {
      detailRequestSequence += 1
      selectedTrack.value = null
      selectedTrackId.value = ''
      detailError.value = ''
      isLoadingDetail.value = false
      clearMapLayers()
    }
  } catch (error) {
    if (requestSequence !== listRequestSequence) return
    tracks.value = []
    listError.value = error.message || '轨迹列表加载失败'
  } finally {
    if (requestSequence === listRequestSequence) isLoadingList.value = false
  }
}

function onFileChange(event) {
  processFile(event.target.files?.[0])
}

function onDrop(event) {
  isDragging.value = false
  processFile(event.dataTransfer.files?.[0])
}

function resetData(clearFile = true) {
  tracks.value = []
  selectedTrack.value = null
  query.value = ''
  page.value = 1
  errorMessage.value = ''
  parseProgress.value = 0
  uploadResult.value = null
  totalTracks.value = 0
  pageCount.value = 1
  isLoadingList.value = false
  isLoadingDetail.value = false
  listError.value = ''
  detailError.value = ''
  selectedTrackId.value = ''
  activeQuery.value = ''
  pageJump.value = '1'
  listRequestSequence += 1
  detailRequestSequence += 1
  if (clearFile) fileName.value = ''
  if (fileInput.value) fileInput.value.value = ''
  clearMapLayers()
}

async function selectTrack(track) {
  if (!uploadResult.value?.upload_id || !track?.id) return
  const requestSequence = ++detailRequestSequence
  selectedTrackId.value = track.id
  selectedTrack.value = null
  detailError.value = ''
  isLoadingDetail.value = true
  clearMapLayers()

  try {
    const payload = await fetchJson(
      `/api/uploads/${uploadResult.value.upload_id}/trajectories/${track.id}`,
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
  if (routeLayer) routeLayer.remove()
  if (endpointLayer) endpointLayer.remove()
  routeLayer = null
  endpointLayer = null
}

function drawTrack(track) {
  initMap()
  clearMapLayers()
  if (!track?.gps?.length) return

  const latLngs = track.gps.map(([longitude, latitude]) => [latitude, longitude])
  routeLayer = L.polyline(latLngs, {
    color: '#176b55',
    weight: 4,
    opacity: 0.94,
    lineCap: 'round',
    lineJoin: 'round',
  }).addTo(map)

  const start = latLngs[0]
  const end = latLngs[latLngs.length - 1]
  endpointLayer = L.layerGroup([
    L.circleMarker(start, { radius: 7, color: '#ffffff', weight: 2, fillColor: '#176b55', fillOpacity: 1 })
      .bindTooltip('起点', { direction: 'top', offset: [0, -6] }),
    L.circleMarker(end, { radius: 7, color: '#ffffff', weight: 2, fillColor: '#c46b3c', fillOpacity: 1 })
      .bindTooltip('终点', { direction: 'top', offset: [0, -6] }),
  ]).addTo(map)

  map.fitBounds(routeLayer.getBounds(), { padding: [54, 54], maxZoom: 16 })
  setTimeout(() => map?.invalidateSize(), 0)
}

function previousPage() {
  if (page.value > 1 && !isLoadingList.value) loadTrajectoryPage(page.value - 1, true)
}

function nextPage() {
  if (page.value < pageCount.value && !isLoadingList.value) loadTrajectoryPage(page.value + 1, true)
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
  if (targetPage !== page.value && !isLoadingList.value) {
    loadTrajectoryPage(targetPage, true)
  }
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
    </header>

    <section class="workspace">
      <aside class="data-panel" aria-label="轨迹数据面板">
        <div class="panel-heading">
          <div>
            <h2>查询轨迹</h2>
          </div>
          <button v-if="fileName" class="icon-button" type="button" title="清除当前数据" @click="resetData()">
            <X :size="17" />
          </button>
        </div>

        <div
          class="upload-zone"
          :class="{ dragging: isDragging }"
          @dragover.prevent="isDragging = true"
          @dragleave.prevent="isDragging = false"
          @drop.prevent="onDrop"
        >
          <input ref="fileInput" type="file" accept=".csv,text/csv" @change="onFileChange" />
          <div class="upload-icon"><Upload :size="20" /></div>
          <div class="upload-copy">
            <strong>{{ fileName || '选择 query_traj_od.csv' }}</strong>
            <span>{{ fileName ? '文件已载入，可重新选择' : '拖放文件，或从本地选择' }}</span>
          </div>
          <button class="primary-button" type="button" @click="fileInput?.click()">
            <FileText :size="16" />{{ fileName ? '更换文件' : '选择文件' }}
          </button>
        </div>

        <div v-if="isParsing" class="status-strip processing">
          <LoaderCircle class="spin" :size="17" />
          <div><strong>正在上传并校验</strong><span>{{ parseProgress }}%</span></div>
          <div class="progress-track"><i :style="{ width: `${parseProgress}%` }"></i></div>
        </div>

        <div v-else-if="errorMessage" class="status-strip error">
          <AlertTriangle :size="18" />
          <div><strong>数据未通过校验</strong><span>{{ errorMessage }}</span></div>
        </div>

        <div v-else-if="uploadResult" class="status-strip valid">
          <CheckCircle2 :size="18" />
          <div><strong>轨迹文件上传成功</strong><span>{{ uploadResult.trajectory_count.toLocaleString() }} 条轨迹</span></div>
        </div>

        <div v-if="uploadResult" class="list-toolbar">
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

        <div class="trajectory-list" role="listbox" aria-label="查询轨迹列表">
          <div v-if="isLoadingList" class="empty-list compact">
            <LoaderCircle class="spin" :size="20" />
            <strong>正在加载轨迹</strong>
          </div>
          <div v-else-if="listError" class="empty-list compact error-copy">
            <AlertTriangle :size="20" />
            <strong>列表加载失败</strong>
            <p>{{ listError }}</p>
          </div>
          <template v-else>
            <button
              v-for="track in visibleTracks"
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
              <strong>没有匹配的轨迹</strong>
              <p>{{ activeQuery }}</p>
            </div>
            <div v-else-if="!tracks.length && !isParsing" class="empty-list">
              <span class="axis-symbol">φ / λ</span>
              <strong>暂无轨迹数据</strong>
              <p>载入查询轨迹文件后，样本将按行编号。</p>
            </div>
          </template>
        </div>

        <nav v-if="uploadResult && totalTracks" class="pagination" aria-label="轨迹列表分页">
          <button class="icon-button" type="button" title="上一页" :disabled="page === 1 || isLoadingList" @click="previousPage">
            <ChevronLeft :size="17" />
          </button>
          <span><b>{{ page }}</b> / {{ pageCount }}</span>
          <button class="icon-button" type="button" title="下一页" :disabled="page === pageCount || isLoadingList" @click="nextPage">
            <ChevronRight :size="17" />
          </button>
          <form class="page-jump" @submit.prevent="jumpToPage">
            <input
              v-model="pageJump"
              type="number"
              min="1"
              :max="pageCount"
              inputmode="numeric"
              aria-label="跳转页码"
            />
            <button class="icon-button" type="submit" title="跳转到指定页" :disabled="isLoadingList">
              <ArrowRight :size="16" />
            </button>
          </form>
        </nav>
      </aside>

      <section class="map-panel" aria-label="轨迹地图">
        <div id="trajectory-map" class="map-canvas"></div>

        <div v-if="selectedTrack" class="map-titlebar">
          <div>
            <p class="section-index">MAP / PORTO</p>
            <h2>{{ selectedTrack.id }}</h2>
          </div>
          <div class="legend"><i></i>查询轨迹</div>
        </div>

        <div v-if="selectionSummary" class="coordinate-strip">
          <div><span>轨迹点</span><strong>{{ selectionSummary.points }}</strong></div>
          <div><span>起始时间</span><strong>{{ selectionSummary.start }}</strong></div>
          <div><span>结束时间</span><strong>{{ selectionSummary.end }}</strong></div>
          <div><span>持续时间</span><strong>{{ selectionSummary.duration }}</strong></div>
        </div>

        <div v-if="isLoadingDetail" class="map-empty">
          <div class="map-empty-grid" aria-hidden="true"></div>
          <LoaderCircle class="spin" :size="28" />
          <h2>正在加载轨迹</h2>
        </div>

        <div v-else-if="detailError" class="map-empty error-copy">
          <div class="map-empty-grid" aria-hidden="true"></div>
          <AlertTriangle :size="28" />
          <h2>轨迹加载失败</h2>
          <p>{{ detailError }}</p>
        </div>

        <div v-else-if="!selectedTrack" class="map-empty">
          <div class="map-empty-grid" aria-hidden="true"></div>
          <MapPinned :size="28" />
          <h2>等待轨迹数据</h2>
          <p>选择 CSV 后，地图将在此显示轨迹形态。</p>
        </div>
      </section>
    </section>
  </main>
</template>
