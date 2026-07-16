<template>
  <div class="simulation-panel">
    <!-- Top Control Bar -->
    <div class="control-bar">
      <div class="status-group">
        <!-- Twitter 平台进度 -->
        <div class="platform-status twitter" :class="{ active: runStatus.twitter_running, completed: runStatus.twitter_completed }">
          <div class="platform-header">
            <svg class="platform-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path>
            </svg>
            <span class="platform-name">信息广场</span>
            <span v-if="runStatus.twitter_completed" class="status-badge">
              <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="3">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
            </span>
          </div>
          <div class="platform-stats">
            <span class="stat">
              <span class="stat-label">轮次</span>
              <span class="stat-value mono">{{ runStatus.twitter_current_round || 0 }}<span class="stat-total">/{{ runStatus.total_rounds || maxRounds || '-' }}</span></span>
            </span>
            <span class="stat">
              <span class="stat-label">模拟时间</span>
              <span class="stat-value mono">{{ twitterElapsedTime }}</span>
            </span>
            <span class="stat">
              <span class="stat-label">动作</span>
              <span class="stat-value mono">{{ runStatus.twitter_actions_count || 0 }}</span>
            </span>
          </div>
          <!-- 可用动作提示 -->
          <div class="actions-tooltip">
            <div class="tooltip-title">可用动作</div>
            <div class="tooltip-actions">
              <span
                v-for="actionType in twitterAvailableActions"
                :key="actionType"
                class="tooltip-action"
                :class="getActionTypeClass(actionType)"
                :title="getActionTypeDescription(actionType)"
              >
                <ActionIcon :type="actionType" :size="11" />
                <span>{{ getActionTypeLabel(actionType) }}</span>
              </span>
            </div>
          </div>
        </div>
        
        <!-- Reddit 平台进度 -->
        <div class="platform-status reddit" :class="{ active: runStatus.reddit_running, completed: runStatus.reddit_completed }">
          <div class="platform-header">
            <svg class="platform-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path>
            </svg>
            <span class="platform-name">话题社区</span>
            <span v-if="runStatus.reddit_completed" class="status-badge">
              <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="3">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
            </span>
          </div>
          <div class="platform-stats">
            <span class="stat">
              <span class="stat-label">轮次</span>
              <span class="stat-value mono">{{ runStatus.reddit_current_round || 0 }}<span class="stat-total">/{{ runStatus.total_rounds || maxRounds || '-' }}</span></span>
            </span>
            <span class="stat">
              <span class="stat-label">模拟时间</span>
              <span class="stat-value mono">{{ redditElapsedTime }}</span>
            </span>
            <span class="stat">
              <span class="stat-label">动作</span>
              <span class="stat-value mono">{{ runStatus.reddit_actions_count || 0 }}</span>
            </span>
          </div>
          <!-- 可用动作提示 -->
          <div class="actions-tooltip">
            <div class="tooltip-title">可用动作</div>
            <div class="tooltip-actions">
              <span
                v-for="actionType in redditAvailableActions"
                :key="actionType"
                class="tooltip-action"
                :class="getActionTypeClass(actionType)"
                :title="getActionTypeDescription(actionType)"
              >
                <ActionIcon :type="actionType" :size="11" />
                <span>{{ getActionTypeLabel(actionType) }}</span>
              </span>
            </div>
          </div>
        </div>
      </div>

      <div class="action-controls">
        <button
          class="action-btn secondary"
          :disabled="isStarting || isGeneratingReport"
          @click="restartSimulation"
        >
          {{ isStarting ? '启动中...' : '重新推演' }}
        </button>
        <button 
          class="action-btn primary"
          :disabled="phase !== 2 || isGeneratingReport"
          @click="handleNextStep"
        >
          <span v-if="isGeneratingReport" class="loading-spinner-small"></span>
          {{ isGeneratingReport ? '启动中...' : '开始生成结果报告' }} 
          <span v-if="!isGeneratingReport" class="arrow-icon">→</span>
        </button>
      </div>
    </div>

    <!-- Main Content: Dual Timeline -->
    <div class="main-content-area" ref="scrollContainer">
      <!-- Timeline Header -->
      <div class="timeline-header" v-if="allActions.length > 0">
        <div class="timeline-stats">
          <span class="total-count">事件总数: <span class="mono">{{ allActions.length }}</span></span>
          <span class="platform-breakdown">
            <span class="breakdown-item twitter">
              <svg class="mini-icon" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>
              <span class="mono">{{ twitterActionsCount }}</span>
            </span>
            <span class="breakdown-divider">/</span>
            <span class="breakdown-item reddit">
              <svg class="mini-icon" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path></svg>
              <span class="mono">{{ redditActionsCount }}</span>
            </span>
          </span>
        </div>
      </div>
      
      <!-- Timeline Feed -->
      <div class="timeline-feed">
        <div class="timeline-axis"></div>
        
        <TransitionGroup name="timeline-item">
          <div 
            v-for="action in chronologicalActions" 
            :key="action._uniqueId || action.id || `${action.timestamp}-${action.agent_id}`" 
            class="timeline-item"
            :class="action.platform"
          >
            <div class="timeline-marker">
              <div class="marker-dot"></div>
            </div>
            
            <div class="timeline-card">
              <div class="card-header">
                <div class="agent-info">
                  <div class="avatar-placeholder">{{ (action.agent_name || 'A')[0] }}</div>
                  <span class="agent-name">{{ action.agent_name }}</span>
                </div>
                
                <div class="header-meta">
                  <span
                    class="action-chip"
                    :class="getActionTypeClass(action.action_type)"
                    :title="getActionTypeDescription(action.action_type)"
                    :aria-label="`动作：${getActionTypeLabel(action.action_type)}`"
                  >
                    <ActionIcon :type="action.action_type" :size="13" />
                    <span>{{ getActionTypeLabel(action.action_type) }}</span>
                  </span>
                  <div class="platform-indicator">
                    <svg v-if="action.platform === 'twitter'" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>
                    <svg v-else viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path></svg>
                  </div>
                </div>
              </div>
              
              <div class="card-body">
                <!-- CREATE_POST: 发布帖子 -->
                <div v-if="action.action_type === 'CREATE_POST' && action.action_args?.content" class="content-text main-text">
                  {{ action.action_args.content }}
                </div>

                <!-- QUOTE_POST: 引用帖子 -->
                <template v-if="action.action_type === 'QUOTE_POST'">
                  <div v-if="action.action_args?.quote_content" class="content-text">
                    {{ action.action_args.quote_content }}
                  </div>
                  <div v-if="action.action_args?.original_content" class="quoted-block">
                    <div class="quote-header">
                      <svg class="icon-small" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path></svg>
                      <span class="quote-label">@{{ action.action_args.original_author_name || 'User' }}</span>
                    </div>
                    <div class="quote-text">
                      {{ truncateContent(action.action_args.original_content, 150) }}
                    </div>
                  </div>
                </template>

                <!-- REPOST: 转发帖子 -->
                <template v-if="action.action_type === 'REPOST'">
                  <div class="repost-info">
                    <svg class="icon-small" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polyline points="17 1 21 5 17 9"></polyline><path d="M3 11V9a4 4 0 0 1 4-4h14"></path><polyline points="7 23 3 19 7 15"></polyline><path d="M21 13v2a4 4 0 0 1-4 4H3"></path></svg>
                    <span class="repost-label">转自 @{{ action.action_args?.original_author_name || '用户' }}</span>
                  </div>
                  <div v-if="action.action_args?.original_content" class="repost-content">
                    {{ truncateContent(action.action_args.original_content, 200) }}
                  </div>
                </template>

                <!-- LIKE_POST / DISLIKE_POST: 点赞或踩帖子 -->
                <template v-if="action.action_type === 'LIKE_POST' || action.action_type === 'DISLIKE_POST'">
                  <div class="like-info">
                    <ActionIcon :type="action.action_type" :size="14" />
                    <span class="like-label">{{ action.action_type === 'LIKE_POST' ? '赞了' : '踩了' }} @{{ action.action_args?.post_author_name || '用户' }} 的帖子</span>
                  </div>
                  <div v-if="action.action_args?.post_content" class="liked-content">
                    "{{ truncateContent(action.action_args.post_content, 120) }}"
                  </div>
                </template>

                <!-- CREATE_COMMENT: 发表评论 -->
                <template v-if="action.action_type === 'CREATE_COMMENT'">
                  <div v-if="action.action_args?.content" class="content-text">
                    {{ action.action_args.content }}
                  </div>
                  <div v-if="action.action_args?.post_id" class="comment-context">
                    <svg class="icon-small" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path></svg>
                    <span>回复帖子 #{{ action.action_args.post_id }}</span>
                  </div>
                </template>

                <!-- LIKE_COMMENT / DISLIKE_COMMENT: 评论互动 -->
                <template v-if="action.action_type === 'LIKE_COMMENT' || action.action_type === 'DISLIKE_COMMENT'">
                  <div class="like-info">
                    <ActionIcon :type="action.action_type" :size="14" />
                    <span class="like-label">{{ action.action_type === 'LIKE_COMMENT' ? '赞了' : '踩了' }} @{{ action.action_args?.comment_author_name || '用户' }} 的评论</span>
                  </div>
                  <div v-if="action.action_args?.comment_content" class="liked-content">
                    "{{ truncateContent(action.action_args.comment_content, 120) }}"
                  </div>
                </template>

                <!-- SEARCH_POSTS / SEARCH_USER: 搜索 -->
                <template v-if="action.action_type === 'SEARCH_POSTS' || action.action_type === 'SEARCH_USER'">
                  <div class="search-info">
                    <ActionIcon :type="action.action_type" :size="14" />
                    <span class="search-label">{{ action.action_type === 'SEARCH_USER' ? '搜索用户:' : '搜索查询:' }}</span>
                    <span class="search-query">"{{ getActionQuery(action) }}"</span>
                  </div>
                </template>

                <!-- FOLLOW: 关注用户 -->
                <template v-if="action.action_type === 'FOLLOW'">
                  <div class="follow-info">
                    <svg class="icon-small" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="8.5" cy="7" r="4"></circle><line x1="20" y1="8" x2="20" y2="14"></line><line x1="23" y1="11" x2="17" y2="11"></line></svg>
                    <span class="follow-label">关注了 @{{ getTargetUserName(action) }}</span>
                  </div>
                </template>

                <!-- MUTE: 静音用户 -->
                <template v-if="action.action_type === 'MUTE'">
                  <div class="follow-info">
                    <ActionIcon :type="action.action_type" :size="14" />
                    <span class="follow-label">静音了 @{{ getTargetUserName(action) }}</span>
                  </div>
                </template>

                <!-- TREND / REFRESH: 浏览信息流动作 -->
                <template v-if="action.action_type === 'TREND' || action.action_type === 'REFRESH'">
                  <div class="search-info">
                    <ActionIcon :type="action.action_type" :size="14" />
                    <span class="search-label">{{ getActionTypeDescription(action.action_type) }}</span>
                  </div>
                </template>

                <!-- UPVOTE / DOWNVOTE -->
                <template v-if="action.action_type === 'UPVOTE_POST' || action.action_type === 'DOWNVOTE_POST'">
                  <div class="vote-info">
                    <svg v-if="action.action_type === 'UPVOTE_POST'" class="icon-small" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polyline points="18 15 12 9 6 15"></polyline></svg>
                    <svg v-else class="icon-small" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"></polyline></svg>
                    <span class="vote-label">{{ action.action_type === 'UPVOTE_POST' ? '赞了' : '踩了' }}帖子</span>
                  </div>
                  <div v-if="action.action_args?.post_content" class="voted-content">
                    "{{ truncateContent(action.action_args.post_content, 120) }}"
                  </div>
                </template>

                <!-- DO_NOTHING: 无操作（静默） -->
                <template v-if="action.action_type === 'DO_NOTHING'">
                  <div class="idle-info">
                    <svg class="icon-small" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
                    <span class="idle-label">跳过操作</span>
                  </div>
                </template>

                <!-- 通用回退：未知类型或有 content 但未被上述处理 -->
                <div v-if="!['CREATE_POST', 'QUOTE_POST', 'REPOST', 'LIKE_POST', 'DISLIKE_POST', 'CREATE_COMMENT', 'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER', 'FOLLOW', 'MUTE', 'TREND', 'REFRESH', 'UPVOTE_POST', 'DOWNVOTE_POST', 'DO_NOTHING'].includes(action.action_type) && action.action_args?.content" class="content-text">
                  {{ action.action_args.content }}
                </div>
              </div>

              <div class="card-footer">
                <span class="time-tag">R{{ action.round_num }} • {{ formatActionTime(action.timestamp) }}</span>
                <!-- Platform tag removed as it is in header now -->
              </div>
            </div>
          </div>
        </TransitionGroup>

        <div v-if="allActions.length === 0" class="waiting-state">
          <div v-if="isStarting || runStatus.runner_status === 'running'" class="pulse-ring"></div>
          <span>{{ emptyStateText }}</span>
        </div>
      </div>
    </div>

    <!-- Bottom Info / Logs -->
    <div v-if="false" class="system-logs">
      <div class="log-header">
        <span class="log-title">模拟监控</span>
        <span class="log-id">{{ simulationId || '无模拟实例' }}</span>
      </div>
      <div class="log-content" ref="logContent">
        <div class="log-line" v-for="(log, idx) in systemLogs" :key="idx">
          <span class="log-time">{{ log.time }}</span>
          <span class="log-msg">{{ log.msg }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick, h } from 'vue'
import { useRouter } from 'vue-router'
import { 
  startSimulation, 
  stopSimulation,
  getRunStatus, 
  getRunStatusDetail,
  getSimulationActions
} from '../api/simulation'
import { generateReport } from '../api/report'

const props = defineProps({
  simulationId: String,
  maxRounds: Number, // 从Step2传入的最大轮数
  minutesPerRound: {
    type: Number,
    default: 30 // 默认每轮30分钟
  },
  projectData: Object,
  graphData: Object,
  systemLogs: Array
})

const emit = defineEmits(['go-back', 'next-step', 'add-log', 'update-status'])

const router = useRouter()

const twitterAvailableActions = ['CREATE_POST', 'LIKE_POST', 'REPOST', 'QUOTE_POST', 'FOLLOW', 'DO_NOTHING']
const redditAvailableActions = [
  'CREATE_POST',
  'CREATE_COMMENT',
  'LIKE_POST',
  'DISLIKE_POST',
  'LIKE_COMMENT',
  'DISLIKE_COMMENT',
  'SEARCH_POSTS',
  'SEARCH_USER',
  'TREND',
  'FOLLOW',
  'MUTE',
  'REFRESH',
  'DO_NOTHING'
]

const ACTION_META = {
  CREATE_POST: {
    label: '发帖',
    description: '发布新的平台内容',
    className: 'badge-post',
    icon: 'post'
  },
  REPOST: {
    label: '转发',
    description: '转发已有帖子',
    className: 'badge-amplify',
    icon: 'repost'
  },
  QUOTE_POST: {
    label: '引用',
    description: '引用原帖并补充评论',
    className: 'badge-amplify',
    icon: 'quote'
  },
  LIKE_POST: {
    label: '点赞',
    description: '点赞一条帖子',
    className: 'badge-react',
    icon: 'heart'
  },
  DISLIKE_POST: {
    label: '踩帖',
    description: '对帖子表达反对',
    className: 'badge-react-negative',
    icon: 'thumb-down'
  },
  CREATE_COMMENT: {
    label: '评论',
    description: '在帖子下发表评论',
    className: 'badge-comment',
    icon: 'comment'
  },
  LIKE_COMMENT: {
    label: '赞评论',
    description: '点赞一条评论',
    className: 'badge-react',
    icon: 'thumb-up'
  },
  DISLIKE_COMMENT: {
    label: '踩评论',
    description: '对评论表达反对',
    className: 'badge-react-negative',
    icon: 'thumb-down'
  },
  SEARCH_POSTS: {
    label: '搜帖',
    description: '搜索帖子或话题内容',
    className: 'badge-meta',
    icon: 'search'
  },
  SEARCH_USER: {
    label: '搜用户',
    description: '搜索平台用户',
    className: 'badge-meta',
    icon: 'user-search'
  },
  TREND: {
    label: '趋势',
    description: '查看趋势话题',
    className: 'badge-meta',
    icon: 'trend'
  },
  FOLLOW: {
    label: '关注',
    description: '关注其他用户',
    className: 'badge-social',
    icon: 'follow'
  },
  MUTE: {
    label: '静音',
    description: '屏蔽或静音用户',
    className: 'badge-moderation',
    icon: 'mute'
  },
  REFRESH: {
    label: '刷新',
    description: '刷新信息流',
    className: 'badge-meta',
    icon: 'refresh'
  },
  DO_NOTHING: {
    label: '空闲',
    description: '本轮未执行社交动作',
    className: 'badge-idle',
    icon: 'idle'
  },
  UPVOTE_POST: {
    label: '顶帖',
    description: '顶一条帖子',
    className: 'badge-react',
    icon: 'chevron-up'
  },
  DOWNVOTE_POST: {
    label: '踩帖',
    description: '踩一条帖子',
    className: 'badge-react-negative',
    icon: 'chevron-down'
  }
}

const iconPaths = {
  post: [
    { type: 'path', attrs: { d: 'M4 4h16v12H7l-3 3V4z' } },
    { type: 'path', attrs: { d: 'M8 8h8' } },
    { type: 'path', attrs: { d: 'M8 12h6' } }
  ],
  repost: [
    { type: 'polyline', attrs: { points: '17 1 21 5 17 9' } },
    { type: 'path', attrs: { d: 'M3 11V9a4 4 0 0 1 4-4h14' } },
    { type: 'polyline', attrs: { points: '7 23 3 19 7 15' } },
    { type: 'path', attrs: { d: 'M21 13v2a4 4 0 0 1-4 4H3' } }
  ],
  quote: [
    { type: 'path', attrs: { d: 'M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.8 1.7' } },
    { type: 'path', attrs: { d: 'M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.8-1.7' } }
  ],
  heart: [
    { type: 'path', attrs: { d: 'M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21.2l7.8-7.8 1-1a5.5 5.5 0 0 0 0-7.8z' } }
  ],
  comment: [
    { type: 'path', attrs: { d: 'M21 11.5a8.5 8.5 0 0 1-12.3 7.6L3 21l1.9-5.7A8.5 8.5 0 1 1 21 11.5z' } }
  ],
  'thumb-up': [
    { type: 'path', attrs: { d: 'M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3' } },
    { type: 'path', attrs: { d: 'M7 11l4-8a3 3 0 0 1 3 3v4h5a2 2 0 0 1 2 2l-1 7a3 3 0 0 1-3 3H7V11z' } }
  ],
  'thumb-down': [
    { type: 'path', attrs: { d: 'M17 2h3a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-3' } },
    { type: 'path', attrs: { d: 'M17 13l-4 8a3 3 0 0 1-3-3v-4H5a2 2 0 0 1-2-2l1-7a3 3 0 0 1 3-3h10v11z' } }
  ],
  search: [
    { type: 'circle', attrs: { cx: '11', cy: '11', r: '8' } },
    { type: 'line', attrs: { x1: '21', y1: '21', x2: '16.65', y2: '16.65' } }
  ],
  'user-search': [
    { type: 'circle', attrs: { cx: '9', cy: '7', r: '4' } },
    { type: 'path', attrs: { d: 'M2 21v-2a4 4 0 0 1 4-4h4' } },
    { type: 'circle', attrs: { cx: '17', cy: '17', r: '3' } },
    { type: 'line', attrs: { x1: '19.5', y1: '19.5', x2: '22', y2: '22' } }
  ],
  trend: [
    { type: 'polyline', attrs: { points: '3 17 9 11 13 15 21 7' } },
    { type: 'polyline', attrs: { points: '15 7 21 7 21 13' } }
  ],
  follow: [
    { type: 'path', attrs: { d: 'M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2' } },
    { type: 'circle', attrs: { cx: '8.5', cy: '7', r: '4' } },
    { type: 'line', attrs: { x1: '20', y1: '8', x2: '20', y2: '14' } },
    { type: 'line', attrs: { x1: '23', y1: '11', x2: '17', y2: '11' } }
  ],
  mute: [
    { type: 'path', attrs: { d: 'M9 9v6h4l5 4V5l-5 4H9z' } },
    { type: 'line', attrs: { x1: '3', y1: '3', x2: '21', y2: '21' } }
  ],
  refresh: [
    { type: 'polyline', attrs: { points: '23 4 23 10 17 10' } },
    { type: 'path', attrs: { d: 'M20.5 15a9 9 0 1 1-2.1-9.4L23 10' } }
  ],
  idle: [
    { type: 'circle', attrs: { cx: '12', cy: '12', r: '10' } },
    { type: 'line', attrs: { x1: '12', y1: '8', x2: '12', y2: '12' } },
    { type: 'line', attrs: { x1: '12', y1: '16', x2: '12.01', y2: '16' } }
  ],
  'chevron-up': [
    { type: 'polyline', attrs: { points: '18 15 12 9 6 15' } }
  ],
  'chevron-down': [
    { type: 'polyline', attrs: { points: '6 9 12 15 18 9' } }
  ],
  default: [
    { type: 'circle', attrs: { cx: '12', cy: '12', r: '10' } },
    { type: 'path', attrs: { d: 'M12 8v4l3 3' } }
  ]
}

const ActionIcon = ({ type, size = 13 }) => {
  const meta = ACTION_META[type] || {}
  const paths = iconPaths[meta.icon] || iconPaths.default
  const fill = meta.icon === 'heart' ? 'currentColor' : 'none'

  return h(
    'svg',
    {
      class: 'action-icon',
      viewBox: '0 0 24 24',
      width: size,
      height: size,
      fill,
      stroke: 'currentColor',
      'stroke-width': 2,
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round',
      'aria-hidden': 'true'
    },
    paths.map((item, index) => h(item.type, { key: `${type || 'unknown'}-${index}`, ...item.attrs }))
  )
}

// State
const isGeneratingReport = ref(false)
const phase = ref(0) // 0: 未开始, 1: 运行中, 2: 已完成
const isStarting = ref(false)
const isStopping = ref(false)
const startError = ref(null)
const runStatus = ref({})
const allActions = ref([]) // 所有动作（增量累积）
const actionIds = ref(new Set()) // 用于去重的动作ID集合
const scrollContainer = ref(null)
const hasLoadedHistory = ref(false)

// Computed
// 按时间顺序显示动作（最新的在最后面，即底部）
const chronologicalActions = computed(() => {
  return allActions.value
})

// 各平台动作计数
const twitterActionsCount = computed(() => {
  return allActions.value.filter(a => a.platform === 'twitter').length
})

const redditActionsCount = computed(() => {
  return allActions.value.filter(a => a.platform === 'reddit').length
})

// 格式化模拟流逝时间（根据轮次和每轮分钟数计算）
const formatElapsedTime = (currentRound) => {
  if (!currentRound || currentRound <= 0) return '0h 0m'
  const totalMinutes = currentRound * props.minutesPerRound
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  return `${hours}h ${minutes}m`
}

// Twitter平台的模拟流逝时间
const twitterElapsedTime = computed(() => {
  return formatElapsedTime(runStatus.value.twitter_current_round || 0)
})

// Reddit平台的模拟流逝时间
const redditElapsedTime = computed(() => {
  return formatElapsedTime(runStatus.value.reddit_current_round || 0)
})

const emptyStateText = computed(() => {
  if (isStarting.value) return '正在启动推演...'
  if (runStatus.value.runner_status === 'running' || runStatus.value.runner_status === 'starting') return '等待智能体动作...'
  if (hasLoadedHistory.value) return '当前推演暂无动作记录'
  return '正在加载推演记录...'
})

// Methods
const addLog = (msg) => {
  emit('add-log', msg)
}

// 重置所有状态（用于重新启动模拟）
const resetAllState = () => {
  phase.value = 0
  runStatus.value = {}
  allActions.value = []
  actionIds.value = new Set()
  prevTwitterRound.value = 0
  prevRedditRound.value = 0
  startError.value = null
  hasLoadedHistory.value = false
  isStarting.value = false
  isStopping.value = false
  stopPolling()  // 停止之前可能存在的轮询
}

const ingestActions = (actions = []) => {
  let newActionsAdded = 0
  actions
    .slice()
    .reverse()
    .forEach(action => {
      const actionId = action.id || `${action.timestamp}-${action.platform}-${action.agent_id}-${action.action_type}-${action.round_num}`
      if (!actionIds.value.has(actionId)) {
        actionIds.value.add(actionId)
        allActions.value.push({
          ...action,
          _uniqueId: actionId
        })
        newActionsAdded++
      }
    })
  return newActionsAdded
}

const loadExistingSimulation = async () => {
  if (!props.simulationId) {
    addLog('错误：缺少 simulationId')
    return
  }

  resetAllState()
  addLog('正在加载推演历史...')

  try {
    const [statusRes, actionsRes] = await Promise.all([
      getRunStatus(props.simulationId),
      getSimulationActions(props.simulationId, { limit: 10000 })
    ])

    if (statusRes.success && statusRes.data) {
      runStatus.value = statusRes.data
      const status = statusRes.data.runner_status
      if (status === 'running' || status === 'starting') {
        phase.value = 1
        emit('update-status', 'processing')
        startStatusPolling()
        startDetailPolling()
      } else if (status && status !== 'idle') {
        phase.value = 2
        emit('update-status', status === 'failed' ? 'error' : 'completed')
      }
    }

    const actions = actionsRes.success ? (actionsRes.data?.actions || []) : []
    ingestActions(actions)
    hasLoadedHistory.value = true

    const runnerStatus = runStatus.value.runner_status
    if (actions.length > 0 && runnerStatus !== 'running' && runnerStatus !== 'starting') {
      phase.value = 2
      emit('update-status', 'completed')
      addLog(`已恢复 ${actions.length} 条历史动作`)
      return
    }

    if (actions.length > 0) {
      addLog(`已恢复 ${actions.length} 条历史动作，继续监听运行状态`)
      return
    }

    if (!runnerStatus || runnerStatus === 'idle') {
      addLog('未发现历史动作，自动启动新推演')
      await doStartSimulation({ force: false })
    } else {
      addLog('推演暂无动作记录')
    }
  } catch (err) {
    hasLoadedHistory.value = true
    startError.value = err.message
    addLog(`加载推演历史失败: ${err.message}`)
    emit('update-status', 'error')
  }
}

// 启动模拟
const doStartSimulation = async ({ force = false } = {}) => {
  if (!props.simulationId) {
    addLog('错误：缺少 simulationId')
    return
  }
  
  // 先重置所有状态，确保不会受到上一次模拟的影响
  resetAllState()
  
  isStarting.value = true
  startError.value = null
  addLog('正在启动双平台并行模拟...')
  emit('update-status', 'processing')
  
  try {
    const params = {
      simulation_id: props.simulationId,
      platform: 'parallel',
      force,
      enable_graph_memory_update: true  // 开启动态图谱更新
    }
    
    if (props.maxRounds) {
      params.max_rounds = props.maxRounds
      addLog(`设置最大模拟轮数: ${props.maxRounds}`)
    }
    
    addLog('已开启动态图谱更新模式')
    
    const res = await startSimulation(params)
    
    if (res.success && res.data) {
      if (res.data.force_restarted) {
        addLog('✓ 已清理旧的模拟日志，重新开始模拟')
      }
      addLog('✓ 模拟引擎启动成功')
      addLog(`  ├─ PID: ${res.data.process_pid || '-'}`)
      
      phase.value = 1
      runStatus.value = res.data
      hasLoadedHistory.value = true
      
      startStatusPolling()
      startDetailPolling()
    } else {
      startError.value = res.error || '启动失败'
      addLog(`✗ 启动失败: ${res.error || '未知错误'}`)
      emit('update-status', 'error')
    }
  } catch (err) {
    startError.value = err.message
    addLog(`✗ 启动异常: ${err.message}`)
    emit('update-status', 'error')
  } finally {
    isStarting.value = false
  }
}

const restartSimulation = async () => {
  addLog('准备重新推演，将清理旧运行日志...')
  await doStartSimulation({ force: true })
}

// 停止模拟
const handleStopSimulation = async () => {
  if (!props.simulationId) return
  
  isStopping.value = true
  addLog('正在停止模拟...')
  
  try {
    const res = await stopSimulation({ simulation_id: props.simulationId })
    
    if (res.success) {
      addLog('✓ 模拟已停止')
      phase.value = 2
      stopPolling()
      emit('update-status', 'completed')
    } else {
      addLog(`停止失败: ${res.error || '未知错误'}`)
    }
  } catch (err) {
    addLog(`停止异常: ${err.message}`)
  } finally {
    isStopping.value = false
  }
}

// 轮询状态
let statusTimer = null
let detailTimer = null

const startStatusPolling = () => {
  if (statusTimer) clearInterval(statusTimer)
  statusTimer = setInterval(fetchRunStatus, 2000)
}

const startDetailPolling = () => {
  if (detailTimer) clearInterval(detailTimer)
  detailTimer = setInterval(fetchRunStatusDetail, 3000)
}

const stopPolling = () => {
  if (statusTimer) {
    clearInterval(statusTimer)
    statusTimer = null
  }
  if (detailTimer) {
    clearInterval(detailTimer)
    detailTimer = null
  }
}

// 追踪各平台的上一次轮次，用于检测变化并输出日志
const prevTwitterRound = ref(0)
const prevRedditRound = ref(0)

const fetchRunStatus = async () => {
  if (!props.simulationId) return
  
  try {
    const res = await getRunStatus(props.simulationId)
    
    if (res.success && res.data) {
      const data = res.data
      
      runStatus.value = data
      
      // 分别检测各平台的轮次变化并输出日志
      if (data.twitter_current_round > prevTwitterRound.value) {
        addLog(`[Plaza] R${data.twitter_current_round}/${data.total_rounds} | T:${data.twitter_simulated_hours || 0}h | A:${data.twitter_actions_count}`)
        prevTwitterRound.value = data.twitter_current_round
      }
      
      if (data.reddit_current_round > prevRedditRound.value) {
        addLog(`[Community] R${data.reddit_current_round}/${data.total_rounds} | T:${data.reddit_simulated_hours || 0}h | A:${data.reddit_actions_count}`)
        prevRedditRound.value = data.reddit_current_round
      }

      // 检测模拟是否失败
      if (data.runner_status === 'failed') {
        const errorMsg = data.error || '模拟运行失败'
        addLog(`✗ 模拟失败: ${errorMsg}`)
        phase.value = 2  // 进入完成阶段（允许查看日志/重试）
        stopPolling()
        emit('update-status', 'error')
        return
      }

      // 检测模拟是否已完成（通过 runner_status 或平台完成状态判断）
      const isCompleted = data.runner_status === 'completed' || data.runner_status === 'stopped'
      
      // 额外检查：如果后端还没来得及更新 runner_status，但平台已经报告完成
      // 通过检测 twitter_completed 和 reddit_completed 状态判断
      const platformsCompleted = checkPlatformsCompleted(data)
      
      if (isCompleted || platformsCompleted) {
        if (platformsCompleted && !isCompleted) {
          addLog('✓ 检测到所有平台模拟已结束')
        }
        addLog('✓ 模拟已完成')
        phase.value = 2
        stopPolling()
        emit('update-status', 'completed')
      }
    }
  } catch (err) {
    console.warn('获取运行状态失败:', err)
  }
}

// 检查所有启用的平台是否已完成
const checkPlatformsCompleted = (data) => {
  // 如果没有任何平台数据，返回 false
  if (!data) return false
  
  // 检查各平台的完成状态
  const twitterCompleted = data.twitter_completed === true
  const redditCompleted = data.reddit_completed === true
  
  // 如果至少有一个平台完成了，检查是否所有启用的平台都完成了
  // 通过 actions_count 判断平台是否被启用（如果 count > 0 或 running 曾为 true）
  const twitterEnabled = (data.twitter_actions_count > 0) || data.twitter_running || twitterCompleted
  const redditEnabled = (data.reddit_actions_count > 0) || data.reddit_running || redditCompleted
  
  // 如果没有任何平台被启用，返回 false
  if (!twitterEnabled && !redditEnabled) return false
  
  // 检查所有启用的平台是否都已完成
  if (twitterEnabled && !twitterCompleted) return false
  if (redditEnabled && !redditCompleted) return false
  
  return true
}

const fetchRunStatusDetail = async () => {
  if (!props.simulationId) return
  
  try {
    const res = await getRunStatusDetail(props.simulationId)
    
    if (res.success && res.data) {
      ingestActions(res.data.all_actions || [])
      
      // 不自动滚动，让用户自由查看时间轴
      // 新动作会在底部追加
    }
  } catch (err) {
    console.warn('获取详细状态失败:', err)
  }
}

// Helpers
const getActionTypeLabel = (type) => {
  return ACTION_META[type]?.label || type || '未知'
}

const getActionTypeClass = (type) => {
  return ACTION_META[type]?.className || 'badge-default'
}

const getActionTypeDescription = (type) => {
  return ACTION_META[type]?.description || `执行 ${type || '未知'} 操作`
}

const getActionQuery = (action) => {
  return action.action_args?.query || action.action_args?.keyword || action.action_args?.username || ''
}

const getTargetUserName = (action) => {
  return action.action_args?.target_user_name
    || action.action_args?.target_user
    || action.action_args?.user_name
    || action.action_args?.user_id
    || action.action_args?.target_id
    || '用户'
}

const truncateContent = (content, maxLength = 100) => {
  if (!content) return ''
  if (content.length > maxLength) return content.substring(0, maxLength) + '...'
  return content
}

const formatActionTime = (timestamp) => {
  if (!timestamp) return ''
  try {
    return new Date(timestamp).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return ''
  }
}

const handleNextStep = async () => {
  if (!props.simulationId) {
    addLog('错误：缺少 simulationId')
    return
  }
  
  if (isGeneratingReport.value) {
    addLog('报告生成请求已发送，请稍候...')
    return
  }
  
  isGeneratingReport.value = true
  addLog('正在启动报告生成...')
  
  try {
    const res = await generateReport({
      simulation_id: props.simulationId,
      force_regenerate: true
    })
    
    if (res.success && res.data) {
      const reportId = res.data.report_id
      addLog(`✓ 报告生成任务已启动: ${reportId}`)
      
      // 跳转到报告页面
      router.push({ name: 'Report', params: { reportId } })
    } else {
      addLog(`✗ 启动报告生成失败: ${res.error || '未知错误'}`)
      isGeneratingReport.value = false
    }
  } catch (err) {
    addLog(`✗ 启动报告生成异常: ${err.message}`)
    isGeneratingReport.value = false
  }
}

// Scroll log to bottom
const logContent = ref(null)
watch(() => props.systemLogs?.length, () => {
  nextTick(() => {
    if (logContent.value) {
      logContent.value.scrollTop = logContent.value.scrollHeight
    }
  })
})

onMounted(() => {
  addLog('Step3 模拟运行初始化')
  if (props.simulationId) {
    loadExistingSimulation()
  }
})

onUnmounted(() => {
  stopPolling()
})
</script>

<style scoped>
.simulation-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #FFFFFF;
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
  overflow: hidden;
}

/* --- Control Bar --- */
.control-bar {
  background: #FFF;
  padding: 12px 24px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid #EAEAEA;
  z-index: 10;
  height: 64px;
}

.status-group {
  display: flex;
  gap: 12px;
}

/* Platform Status Cards */
.platform-status {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px 12px;
  border-radius: 4px;
  background: #FAFAFA;
  border: 1px solid #EAEAEA;
  opacity: 0.7;
  transition: all 0.3s;
  min-width: 140px;
  position: relative;
  cursor: pointer;
}

.platform-status.active {
  opacity: 1;
  border-color: #333;
  background: #FFF;
}

.platform-status.completed {
  opacity: 1;
  border-color: #1A936F;
  background: #F2FAF6;
}

/* Actions Tooltip */
.actions-tooltip {
  position: absolute;
  top: 100%;
  left: 50%;
  transform: translateX(-50%);
  margin-top: 8px;
  padding: 10px 14px;
  background: #000;
  color: #FFF;
  border-radius: 4px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  opacity: 0;
  visibility: hidden;
  transition: all 0.2s ease;
  z-index: 100;
  min-width: 180px;
  pointer-events: none;
}

.actions-tooltip::before {
  content: '';
  position: absolute;
  top: -6px;
  left: 50%;
  transform: translateX(-50%);
  border-left: 6px solid transparent;
  border-right: 6px solid transparent;
  border-bottom: 6px solid #000;
}

.platform-status:hover .actions-tooltip {
  opacity: 1;
  visibility: visible;
}

.tooltip-title {
  font-size: 10px;
  font-weight: 600;
  color: #999;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 8px;
}

.tooltip-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tooltip-action {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  font-weight: 600;
  padding: 4px 7px;
  background: rgba(255, 255, 255, 0.15);
  border-radius: 2px;
  color: #FFF;
  border: 1px solid rgba(255, 255, 255, 0.16);
  letter-spacing: 0;
  line-height: 1;
}

.tooltip-action .action-icon {
  flex: 0 0 auto;
  opacity: 0.92;
}

.platform-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 2px;
}

.platform-name {
  font-size: 11px;
  font-weight: 700;
  color: #000;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.platform-status.twitter .platform-icon { color: #000; }
.platform-status.reddit .platform-icon { color: #000; }

.platform-stats {
  display: flex;
  gap: 10px;
}

.stat {
  display: flex;
  align-items: baseline;
  gap: 3px;
}

.stat-label {
  font-size: 8px;
  color: #999;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.stat-value {
  font-size: 11px;
  font-weight: 600;
  color: #333;
}

.stat-total, .stat-unit {
  font-size: 9px;
  color: #999;
  font-weight: 400;
}

.status-badge {
  margin-left: auto;
  color: #1A936F;
  display: flex;
  align-items: center;
}

/* Action Button */
.action-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 20px;
  font-size: 13px;
  font-weight: 600;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s ease;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.action-btn.primary {
  background: #000;
  color: #FFF;
}

.action-btn.primary:hover:not(:disabled) {
  background: #333;
}

.action-btn.secondary {
  background: #FFF;
  color: #111827;
  border: 1px solid #D1D5DB;
}

.action-btn.secondary:hover:not(:disabled) {
  background: #F3F4F6;
  border-color: #9CA3AF;
}

.action-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

/* --- Main Content Area --- */
.main-content-area {
  flex: 1;
  overflow-y: auto;
  position: relative;
  background: #FFF;
}

/* Timeline Header */
.timeline-header {
  position: sticky;
  top: 0;
  background: rgba(255, 255, 255, 0.9);
  backdrop-filter: blur(8px);
  padding: 12px 24px;
  border-bottom: 1px solid #EAEAEA;
  z-index: 5;
  display: flex;
  justify-content: center;
}

.timeline-stats {
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 11px;
  color: #666;
  background: #F5F5F5;
  padding: 4px 12px;
  border-radius: 20px;
}

.total-count {
  font-weight: 600;
  color: #333;
}

.platform-breakdown {
  display: flex;
  align-items: center;
  gap: 8px;
}

.breakdown-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.breakdown-divider { color: #DDD; }
.breakdown-item.twitter { color: #000; }
.breakdown-item.reddit { color: #000; }

/* --- Timeline Feed --- */
.timeline-feed {
  padding: 24px 0;
  position: relative;
  min-height: 100%;
  max-width: 900px;
  margin: 0 auto;
}

.timeline-axis {
  position: absolute;
  left: 50%;
  top: 0;
  bottom: 0;
  width: 1px;
  background: #EAEAEA; /* Cleaner line */
  transform: translateX(-50%);
}

.timeline-item {
  display: flex;
  justify-content: center;
  margin-bottom: 32px;
  position: relative;
  width: 100%;
}

.timeline-marker {
  position: absolute;
  left: 50%;
  top: 24px;
  width: 10px;
  height: 10px;
  background: #FFF;
  border: 1px solid #CCC;
  border-radius: 50%;
  transform: translateX(-50%);
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
}

.marker-dot {
  width: 4px;
  height: 4px;
  background: #CCC;
  border-radius: 50%;
}

.timeline-item.twitter .marker-dot { background: #000; }
.timeline-item.reddit .marker-dot { background: #000; }
.timeline-item.twitter .timeline-marker { border-color: #000; }
.timeline-item.reddit .timeline-marker { border-color: #000; }

/* Card Layout */
.timeline-card {
  width: calc(100% - 48px);
  background: #FFF;
  border-radius: 2px;
  padding: 16px 20px;
  border: 1px solid #EAEAEA;
  box-shadow: 0 2px 10px rgba(0,0,0,0.02);
  position: relative;
  transition: all 0.2s;
}

.timeline-card:hover {
  box-shadow: 0 4px 12px rgba(0,0,0,0.05);
  border-color: #DDD;
}

/* Left side (Twitter) */
.timeline-item.twitter {
  justify-content: flex-start;
  padding-right: 50%;
}
.timeline-item.twitter .timeline-card {
  margin-left: auto;
  margin-right: 32px; /* Gap from axis */
}

/* Right side (Reddit) */
.timeline-item.reddit {
  justify-content: flex-end;
  padding-left: 50%;
}
.timeline-item.reddit .timeline-card {
  margin-right: auto;
  margin-left: 32px; /* Gap from axis */
}

/* Card Content Styles */
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid #F5F5F5;
}

.agent-info {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.avatar-placeholder {
  width: 24px;
  height: 24px;
  background: #000;
  color: #FFF;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
}

.agent-name {
  font-size: 13px;
  font-weight: 600;
  color: #000;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.header-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
  margin-left: 12px;
}

.platform-indicator {
  color: #999;
  display: flex;
  align-items: center;
}

.action-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-height: 24px;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0;
  line-height: 1;
  white-space: nowrap;
  border: 1px solid transparent;
}

.action-icon {
  flex: 0 0 auto;
}

/* Action Badges */
.badge-post { background: #EEF2FF; color: #3730A3; border-color: #C7D2FE; }
.badge-comment { background: #ECFDF5; color: #047857; border-color: #A7F3D0; }
.badge-amplify { background: #FFF7ED; color: #C2410C; border-color: #FED7AA; }
.badge-react { background: #FFF1F2; color: #BE123C; border-color: #FECDD3; }
.badge-react-negative { background: #F8FAFC; color: #475569; border-color: #CBD5E1; }
.badge-social { background: #EFF6FF; color: #1D4ED8; border-color: #BFDBFE; }
.badge-meta { background: #F5F3FF; color: #6D28D9; border-color: #DDD6FE; }
.badge-moderation { background: #F4F4F5; color: #3F3F46; border-color: #D4D4D8; }
.badge-idle { background: #F8FAFC; color: #94A3B8; border-color: #E2E8F0; }
.badge-default { background: #FAFAFA; color: #525252; border-color: #E5E5E5; }

.content-text {
  font-size: 13px;
  line-height: 1.6;
  color: #333;
  margin-bottom: 10px;
}

.content-text.main-text {
  font-size: 14px;
  color: #000;
}

/* Info Blocks (Quote, Repost, etc) */
.quoted-block, .repost-content {
  background: #F9F9F9;
  border: 1px solid #EEE;
  padding: 10px 12px;
  border-radius: 2px;
  margin-top: 8px;
  font-size: 12px;
  color: #555;
}

.quote-header, .repost-info, .like-info, .search-info, .follow-info, .vote-info, .idle-info, .comment-context {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  font-size: 11px;
  color: #666;
}

.icon-small {
  color: #999;
}
.icon-small.filled {
  color: #999; /* Keep icons neutral unless highlighted */
}

.search-query {
  font-family: 'JetBrains Mono', monospace;
  background: #F0F0F0;
  padding: 0 4px;
  border-radius: 2px;
}

.card-footer {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
  font-size: 10px;
  color: #BBB;
  font-family: 'JetBrains Mono', monospace;
}

/* Waiting State */
.waiting-state {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  color: #CCC;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

.pulse-ring {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: 1px solid #EAEAEA;
  animation: ripple 2s infinite;
}

@keyframes ripple {
  0% { transform: scale(0.8); opacity: 1; border-color: #CCC; }
  100% { transform: scale(2.5); opacity: 0; border-color: #EAEAEA; }
}

/* Animation */
.timeline-item-enter-active,
.timeline-item-leave-active {
  transition: all 0.4s cubic-bezier(0.165, 0.84, 0.44, 1);
}

.timeline-item-enter-from {
  opacity: 0;
  transform: translateY(20px);
}

.timeline-item-leave-to {
  opacity: 0;
}

/* Logs */
.system-logs {
  background: #000;
  color: #DDD;
  padding: 16px;
  font-family: 'JetBrains Mono', monospace;
  border-top: 1px solid #222;
  flex-shrink: 0;
}

.log-header {
  display: flex;
  justify-content: space-between;
  border-bottom: 1px solid #333;
  padding-bottom: 8px;
  margin-bottom: 8px;
  font-size: 10px;
  color: #666;
}

.log-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
  height: 100px;
  overflow-y: auto;
  padding-right: 4px;
}

.log-content::-webkit-scrollbar { width: 4px; }
.log-content::-webkit-scrollbar-thumb { background: #333; border-radius: 2px; }

.log-line {
  font-size: 11px;
  display: flex;
  gap: 12px;
  line-height: 1.5;
}

.log-time { color: #555; min-width: 75px; }
.log-msg { color: #BBB; word-break: break-all; }
.mono { font-family: 'JetBrains Mono', monospace; }

/* Loading spinner for button */
.loading-spinner-small {
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #FFF;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin-right: 6px;
}
</style>
