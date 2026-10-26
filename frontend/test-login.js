// 测试登录流程
const axios = require('axios');

async function testLogin() {
  try {
    // 1. 登录
    console.log('1. 测试登录...');
    const loginResponse = await axios.post('http://localhost:8000/api/auth/login', 
      new URLSearchParams({
        username: 'admin',
        password: process.env.ANNOTATION_PASSWORD
      }),
      {
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded'
        }
      }
    );
    console.log('✅ 登录成功');


    const token = loginResponse.data.access_token;

    // 2. 获取用户信息
    console.log('\n2. 测试获取用户信息...');
    const userResponse = await axios.get('http://localhost:8000/api/auth/me', {
      headers: {
        'Authorization': `Bearer ${token}`
      }
    });
    console.log('✅ 获取用户信息成功');
    console.log('用户:', JSON.stringify(userResponse.data, null, 2));

    // 3. 获取项目列表
    console.log('\n3. 测试获取项目列表...');
    const projectsResponse = await axios.get('http://localhost:8000/api/projects', {
      headers: {
        'Authorization': `Bearer ${token}`
      },
      params: {
        page: 1,
        page_size: 5
      }
    });
    console.log('✅ 获取项目列表成功');
    console.log(`项目总数: ${projectsResponse.data.total}`);

    console.log('\n🎉 所有测试通过！');
  } catch (error) {
    console.error('❌ 测试失败:', error.response?.data || error.message);
  }
}

testLogin();
