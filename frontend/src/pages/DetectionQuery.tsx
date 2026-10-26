import React, { useState, useEffect } from 'react';
import {
  Card,
  Form,
  Select,
  DatePicker,
  Button,
  Table,
  Space,
  message,
  Row,
  Col,
  Input,
  Tag
} from 'antd';
import { SearchOutlined, ReloadOutlined, DownloadOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { detectionApi } from '../services/detections';

const { RangePicker } = DatePicker;
const { Option } = Select;

interface PlatformOption {
  platform_id: string;
  platform_name: string;
}

interface DetectionTargetOption {
  target_id: string;
  target_name: string;
}

interface DetectionItem {
  platform: string;
  data_type: string;
  detection_target: string;
  device_id: string;
  detection_time: string;
  total_count: number;
}

const DetectionQuery: React.FC = () => {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [platforms, setPlatforms] = useState<PlatformOption[]>([]);
  const [detectionTargets, setDetectionTargets] = useState<DetectionTargetOption[]>([]);
  const [detections, setDetections] = useState<DetectionItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  // 加载平台和检测目标选项
  useEffect(() => {
    loadOptions();
  }, []);

  const loadOptions = async () => {
    try {
      const [platformsRes, targetsRes] = await Promise.all([
        detectionApi.getPlatforms(),
        detectionApi.getDetectionTargets()
      ]);
      setPlatforms(platformsRes);
      setDetectionTargets(targetsRes);
    } catch (error) {
      message.error('加载选项失败');
    }
  };

  // 查询检测结果
  const handleSearch = async () => {
    setLoading(true);
    try {
      const values = await form.validateFields();

      const params: any = {
        page,
        page_size: pageSize
      };

      if (values.platforms && values.platforms.length > 0) {
        params.platforms = values.platforms;
      }
      if (values.data_types && values.data_types.length > 0) {
        params.data_types = values.data_types;
      }
      if (values.detection_targets && values.detection_targets.length > 0) {
        params.detection_targets = values.detection_targets;
      }
      if (values.equipment) {
        params.equipment = values.equipment;
      }
      if (values.time_range && values.time_range.length === 2) {
        params.start_time = values.time_range[0].toISOString();
        params.end_time = values.time_range[1].toISOString();
      }

      const response = await detectionApi.queryDetections(params);
      setDetections(response.items);
      setTotal(response.total);
      message.success(`查询成功，共找到 ${response.total} 条记录`);
    } catch (error) {
      message.error('查询失败');
    } finally {
      setLoading(false);
    }
  };

  // 重置表单
  const handleReset = () => {
    form.resetFields();
    setDetections([]);
    setTotal(0);
    setPage(1);
  };

  // 下载结果
  const handleDownload = () => {
    message.info('下载功能开发中...');
  };

  const columns = [
    {
      title: '平台',
      dataIndex: 'platform',
      key: 'platform',
      width: 120,
      render: (platform: string) => {
        const platformMap: Record<string, string> = {
          'xiaohongshu': '小红书',
          'douyin': '抖音',
          'weibo': '微博',
          'bilibili': '哔哩哔哩'
        };
        return <Tag color="blue">{platformMap[platform] || platform}</Tag>;
      }
    },
    {
      title: '数据类型',
      dataIndex: 'data_type',
      key: 'data_type',
      width: 100,
      render: (type: string) => {
        const typeMap: Record<string, { text: string; color: string }> = {
          'image': { text: '图片', color: 'green' },
          'video': { text: '视频', color: 'orange' },
          'text': { text: '文本', color: 'default' }
        };
        const info = typeMap[type] || { text: type, color: 'default' };
        return <Tag color={info.color}>{info.text}</Tag>;
      }
    },
    {
      title: '检测目标',
      dataIndex: 'detection_target',
      key: 'detection_target',
      width: 150,
      render: (target: string) => {
        const targetObj = detectionTargets.find(t => t.target_id === target);
        return targetObj ? targetObj.target_name : target;
      }
    },
    {
      title: 'DEVICEID',
      dataIndex: 'device_id',
      key: 'device_id',
      width: 150
    },
    {
      title: '检测时间',
      dataIndex: 'detection_time',
      key: 'detection_time',
      width: 180,
      render: (time: string) => dayjs(time).format('YYYY-MM-DD HH:mm:ss')
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: () => (
        <Space>
          <Button type="link" size="small">详情</Button>
        </Space>
      )
    }
  ];

  return (
    <div style={{ padding: '24px' }}>
      <Card
        title="目标检测结果查询"
        extra={
          <Button
            type="primary"
            icon={<ReloadOutlined />}
            onClick={loadOptions}
          >
            同步配置
          </Button>
        }
        style={{ marginBottom: '16px' }}
      >
        <div style={{ color: '#666', marginBottom: '24px' }}>
          查询内网各业务平台的目标检测记录，支持多平台批量查询
        </div>

        <Form
          form={form}
          layout="vertical"
          initialValues={{
            time_range: [dayjs().startOf('day'), dayjs()]
          }}
        >
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item
                label="平台（最多10个）"
                name="platforms"
              >
                <Select
                  mode="multiple"
                  placeholder="请选择平台(可多选)"
                  allowClear
                  maxTagCount={3}
                  showSearch
                  optionFilterProp="children"
                >
                  {platforms.map(p => (
                    <Option key={p.platform_id} value={p.platform_id}>
                      {p.platform_name}
                    </Option>
                  ))}
                </Select>
              </Form.Item>
            </Col>

            <Col span={8}>
              <Form.Item
                label="数据类型"
                name="data_types"
              >
                <Select
                  mode="multiple"
                  placeholder="请选择数据类型"
                  allowClear
                >
                  <Option value="image">图片数据</Option>
                  <Option value="video">视频数据</Option>
                  <Option value="text">文本数据</Option>
                </Select>
              </Form.Item>
            </Col>

            <Col span={8}>
              <Form.Item
                label="检测目标（全部目标）"
                name="detection_targets"
              >
                <Select
                  mode="multiple"
                  placeholder="请选择检测目标(可多选)"
                  allowClear
                  maxTagCount={2}
                  showSearch
                  optionFilterProp="children"
                >
                  {detectionTargets.map(t => (
                    <Option key={t.target_id} value={t.target_id}>
                      {t.target_name}
                    </Option>
                  ))}
                </Select>
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={16}>
            <Col span={8}>
              <Form.Item
                label="设备"
                name="equipment"
              >
                <Input placeholder="例如: CAM-001" />
              </Form.Item>
            </Col>

            <Col span={8}>
              <Form.Item
                label="开始时间"
                name="time_range"
              >
                <RangePicker
                  showTime
                  format="YYYY-MM-DD HH:mm:ss"
                  style={{ width: '100%' }}
                />
              </Form.Item>
            </Col>

            <Col span={8} style={{ display: 'flex', alignItems: 'flex-end' }}>
              <Form.Item style={{ marginBottom: 0, width: '100%' }}>
                <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
                  <Button onClick={handleReset}>
                    重置
                  </Button>
                  <Button
                    type="primary"
                    icon={<SearchOutlined />}
                    onClick={handleSearch}
                    loading={loading}
                  >
                    查询
                  </Button>
                </Space>
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Card>

      <Card
        title={`检测结果 共找到 ${total} 条记录`}
        extra={
          <Button
            icon={<DownloadOutlined />}
            onClick={handleDownload}
            disabled={detections.length === 0}
          >
            下载所有图片
          </Button>
        }
      >
        <Table
          columns={columns}
          dataSource={detections}
          loading={loading}
          rowKey={(record, index) => `${record.device_id}-${index}`}
          pagination={{
            current: page,
            pageSize: pageSize,
            total: total,
            showSizeChanger: true,
            showQuickJumper: true,
            showTotal: (total) => `显示 ${total} 条 | 共 ${Math.ceil(total / pageSize)} 条结果`,
            onChange: (page, pageSize) => {
              setPage(page);
              setPageSize(pageSize);
            }
          }}
        />
      </Card>
    </div>
  );
};

export default DetectionQuery;
