/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 */
import React, { Component } from 'react';
import {Switch, Route} from 'react-router-dom';
import Login from './pages/login';
import WebSSH from './pages/ssh';
import Layout from './layout';
import {ResourceAccessProvider} from './resources/ResourceAccess';

const AuthorizedLayout = props => <ResourceAccessProvider><Layout {...props}/></ResourceAccessProvider>;
const AuthorizedSSH = props => <ResourceAccessProvider><WebSSH {...props}/></ResourceAccessProvider>;

class App extends Component {
  render() {
    return (
      <Switch>
        <Route path="/" exact component={Login} />
        <Route path="/ssh" exact component={AuthorizedSSH} />
        <Route component={AuthorizedLayout} />
      </Switch>
    );
  }
}

export default App;
