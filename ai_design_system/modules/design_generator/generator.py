class DesignGenerator:
    def generate(self, feature_vector: dict) -> dict:
        """
        基于特征向量生成设计方案
        
        Args:
            feature_vector: 包含系统特征的字典，如功能需求、性能要求、用户类型等
        
        Returns:
            包含以下结构的设计方案字典：
            {
                'components': [...],      # 组件列表
                'interactions': [...],    # 交互流程
                'interfaces': [...]       # 接口定义
            }
        """
        design_plan = {
            'components': self._generate_components(feature_vector),
            'interactions': self._generate_interactions(feature_vector),
            'interfaces': self._generate_interfaces(feature_vector)
        }
        return design_plan
    
    def _generate_components(self, feature_vector: dict) -> list:
        """
        基于特征向量生成组件列表
        
        Args:
            feature_vector: 系统特征向量
        
        Returns:
            组件列表
        """
        components = []
        
        # 根据功能需求生成组件
        if feature_vector.get('requires_authentication', False):
            components.append({
                'name': 'AuthenticationService',
                'type': 'service',
                'responsibility': '用户认证和授权'
            })
        
        if feature_vector.get('requires_data_persistence', False):
            components.append({
                'name': 'DataRepository',
                'type': 'repository',
                'responsibility': '数据持久化和存取'
            })
        
        if feature_vector.get('requires_caching', False):
            components.append({
                'name': 'CacheManager',
                'type': 'service',
                'responsibility': '缓存管理和优化'
            })
        
        if feature_vector.get('requires_api_gateway', False):
            components.append({
                'name': 'APIGateway',
                'type': 'gateway',
                'responsibility': 'API路由和请求转发'
            })
        
        if feature_vector.get('requires_notification', False):
            components.append({
                'name': 'NotificationService',
                'type': 'service',
                'responsibility': '消息通知和推送'
            })
        
        # 基础UI组件
        if feature_vector.get('ui_type') in ['web', 'hybrid']:
            components.append({
                'name': 'UIController',
                'type': 'controller',
                'responsibility': '用户界面展示和交互'
            })
        
        return components
    
    def _generate_interactions(self, feature_vector: dict) -> list:
        """
        基于特征向量生成交互流程
        
        Args:
            feature_vector: 系统特征向量
        
        Returns:
            交互流程列表
        """
        interactions = []
        
        # 认证流程
        if feature_vector.get('requires_authentication', False):
            interactions.append({
                'name': '用户认证流程',
                'steps': [
                    '用户输入凭证',
                    'UIController将凭证发送到AuthenticationService',
                    'AuthenticationService验证凭证',
                    '返回认证结果和token'
                ],
                'participants': ['User', 'UIController', 'AuthenticationService']
            })
        
        # 数据获取流程
        if feature_vector.get('requires_data_persistence', False):
            interactions.append({
                'name': '数据获取流程',
                'steps': [
                    '客户端请求数据',
                    '检查缓存（如果启用）',
                    '若缓存未命中，查询DataRepository',
                    '返回数据并更新缓存'
                ],
                'participants': ['Client', 'CacheManager', 'DataRepository']
            })
        
        # API调用流程
        if feature_vector.get('requires_api_gateway', False):
            interactions.append({
                'name': 'API请求流程',
                'steps': [
                    '请求通过APIGateway接收',
                    '执行身份验证和授权检查',
                    '路由到对应的服务处理',
                    '返回响应给客户端'
                ],
                'participants': ['Client', 'APIGateway', 'Service']
            })
        
        # 通知流程
        if feature_vector.get('requires_notification', False):
            interactions.append({
                'name': '通知发送流程',
                'steps': [
                    '系统事件触发',
                    'NotificationService处理事件',
                    '组装通知内容',
                    '通过多个渠道发送通知'
                ],
                'participants': ['System', 'NotificationService', 'User']
            })
        
        return interactions
    
    def _generate_interfaces(self, feature_vector: dict) -> list:
        """
        基于特征向量生成接口定义
        
        Args:
            feature_vector: 系统特征向量
        
        Returns:
            接口定义列表
        """
        interfaces = []
        
        # 认证接口
        if feature_vector.get('requires_authentication', False):
            interfaces.append({
                'name': 'IAuthenticationService',
                'methods': [
                    {
                        'name': 'authenticate',
                        'params': ['username: str', 'password: str'],
                        'return': 'AuthToken'
                    },
                    {
                        'name': 'verify_token',
                        'params': ['token: str'],
                        'return': 'bool'
                    },
                    {
                        'name': 'logout',
                        'params': ['token: str'],
                        'return': 'bool'
                    }
                ]
            })
        
        # 数据访问接口
        if feature_vector.get('requires_data_persistence', False):
            interfaces.append({
                'name': 'IDataRepository',
                'methods': [
                    {
                        'name': 'query',
                        'params': ['query: dict'],
                        'return': 'List[Entity]'
                    },
                    {
                        'name': 'create',
                        'params': ['entity: Entity'],
                        'return': 'Entity'
                    },
                    {
                        'name': 'update',
                        'params': ['entity: Entity'],
                        'return': 'Entity'
                    },
                    {
                        'name': 'delete',
                        'params': ['id: str'],
                        'return': 'bool'
                    }
                ]
            })
        
        # 缓存接口
        if feature_vector.get('requires_caching', False):
            interfaces.append({
                'name': 'ICacheManager',
                'methods': [
                    {
                        'name': 'get',
                        'params': ['key: str'],
                        'return': 'Any'
                    },
                    {
                        'name': 'set',
                        'params': ['key: str', 'value: Any', 'ttl: int'],
                        'return': 'bool'
                    },
                    {
                        'name': 'delete',
                        'params': ['key: str'],
                        'return': 'bool'
                    },
                    {
                        'name': 'clear',
                        'params': [],
                        'return': 'bool'
                    }
                ]
            })
        
        # API网关接口
        if feature_vector.get('requires_api_gateway', False):
            interfaces.append({
                'name': 'IAPIGateway',
                'methods': [
                    {
                        'name': 'route_request',
                        'params': ['request: Request'],
                        'return': 'Response'
                    },
                    {
                        'name': 'authorize',
                        'params': ['request: Request'],
                        'return': 'bool'
                    },
                    {
                        'name': 'rate_limit',
                        'params': ['client_id: str'],
                        'return': 'bool'
                    }
                ]
            })
        
        # 通知接口
        if feature_vector.get('requires_notification', False):
            interfaces.append({
                'name': 'INotificationService',
                'methods': [
                    {
                        'name': 'send_notification',
                        'params': ['recipient: str', 'message: str', 'channel: str'],
                        'return': 'bool'
                    },
                    {
                        'name': 'subscribe',
                        'params': ['user_id: str', 'channel: str'],
                        'return': 'bool'
                    },
                    {
                        'name': 'unsubscribe',
                        'params': ['user_id: str', 'channel: str'],
                        'return': 'bool'
                    }
                ]
            })
        
        return interfaces
